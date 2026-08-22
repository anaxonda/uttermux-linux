#include "sd_sherpa/audio.hpp"
#include "uttermux/protocol.hpp"
#include "uttermux/text.hpp"

#include <speech-dispatcher/spd_module_main.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <utility>
#include <vector>

using namespace uttermux;

namespace {
struct BrokerVoice { std::string id, name, language, variant; };
std::vector<BrokerVoice> voices;
std::vector<SPDVoice *> speechd_voices;
std::string current_voice, current_language;
int current_rate = 0, current_pitch = 0, current_volume = 0;
int broker_fd = -1;
std::mutex state_mutex, send_mutex;
std::thread worker;
std::atomic<uint64_t> current_request{0};
std::atomic<bool> stopped{false}, paused{false};
std::atomic<pid_t> player_pid{-1};
uint64_t next_request = 10;

char *copy(const std::string &text) { return ::strdup(text.c_str()); }
void free_voices() {
  for (auto *voice : speechd_voices) {
    if (!voice) continue;
    std::free(voice->name); std::free(voice->language); std::free(voice->variant); std::free(voice);
  }
  speechd_voices.clear(); voices.clear();
}

bool send_locked(Message type, uint64_t id, const void *data = nullptr, size_t size = 0) {
  std::lock_guard<std::mutex> lock(send_mutex);
  return send_packet(broker_fd, type, id, data, size);
}

void connect_and_list() {
  if (broker_fd >= 0) ::close(broker_fd);
  broker_fd = connect_broker();
  if (broker_fd < 0) throw std::runtime_error("cannot connect to uttermuxd");
  constexpr uint64_t request = 1;
  auto purpose = fields({"speechd"});
  if (!send_locked(Message::ListVoices, request, purpose.data(), purpose.size()))
    throw std::runtime_error("cannot request UtterMux voices");
  free_voices();
  Packet response;
  while (receive_packet(broker_fd, &response)) {
    if (response.request_id != request) continue;
    if (response.type == Message::Done) break;
    if (response.type == Message::Error)
      throw std::runtime_error(std::string(response.payload.begin(), response.payload.end()));
    if (response.type != Message::Voice) continue;
    auto values = split_fields(response.payload);
    if (values.size() < 4) throw std::runtime_error("invalid voice record from uttermuxd");
    voices.push_back({values[0], values[1], values[2], values[3]});
  }
  if (voices.empty()) throw std::runtime_error("uttermuxd returned no voices");
  for (const auto &voice : voices) {
    auto *item = static_cast<SPDVoice *>(std::calloc(1, sizeof(SPDVoice)));
    if (!item) throw std::bad_alloc();
    item->name = copy(voice.name); item->language = copy(voice.language); item->variant = copy(voice.variant);
    speechd_voices.push_back(item);
  }
  speechd_voices.push_back(nullptr);
}

const BrokerVoice *resolve_voice(const std::string &name) {
  for (const auto &voice : voices) if (voice.name == name || voice.id == name) return &voice;
  return nullptr;
}

class PcmPlayer {
 public:
  explicit PcmPlayer(int sample_rate) {
    rate_argument_ = "--rate=" + std::to_string(sample_rate);
    int input[2];
    if (::pipe(input) != 0) throw std::runtime_error("cannot create audio pipe");
    child_ = ::fork();
    if (child_ < 0) {
      ::close(input[0]); ::close(input[1]);
      throw std::runtime_error("cannot start paplay");
    }
    if (child_ == 0) {
      ::dup2(input[0], STDIN_FILENO); ::close(input[0]); ::close(input[1]);
      ::execlp("paplay", "paplay", "--raw", "--format=s16le", "--channels=1",
               rate_argument_.c_str(), "--stream-name=UtterMux", static_cast<char *>(nullptr));
      _exit(127);
    }
    ::close(input[0]); input_ = input[1]; player_pid.store(child_);
  }

  ~PcmPlayer() {
    if (input_ >= 0) ::close(input_);
    if (child_ > 0) {
      ::kill(child_, SIGTERM);
      while (::waitpid(child_, nullptr, 0) < 0 && errno == EINTR) {}
      pid_t expected = child_; player_pid.compare_exchange_strong(expected, -1);
    }
  }

  void write(const std::vector<int16_t> &pcm) {
    const auto *bytes = reinterpret_cast<const uint8_t *>(pcm.data());
    size_t remaining = pcm.size() * sizeof(int16_t);
    while (remaining && !stopped.load()) {
      ssize_t written = ::write(input_, bytes, remaining);
      if (written > 0) { bytes += written; remaining -= static_cast<size_t>(written); }
      else if (written < 0 && errno == EINTR) continue;
      else break;
    }
  }

  void finish() {
    if (input_ >= 0) { ::close(input_); input_ = -1; }
    int status = 0;
    while (::waitpid(child_, &status, 0) < 0 && errno == EINTR) {}
    pid_t expected = child_; player_pid.compare_exchange_strong(expected, -1);
    child_ = -1;
    if (!stopped.load() && (!WIFEXITED(status) || WEXITSTATUS(status) != 0))
      throw std::runtime_error("paplay failed");
  }

 private:
  pid_t child_ = -1;
  int input_ = -1;
  std::string rate_argument_;
};

void speak_worker(std::string text, std::string voice_id, std::string language,
                  int rate, int pitch, int volume, uint64_t request) {
  bool began = false, success = false;
  int sample_rate = 0;
  uint8_t sample_format = 1;
  std::vector<uint8_t> pending_audio;
  std::unique_ptr<sdsherpa::AudioProcessor> processor;
  std::unique_ptr<PcmPlayer> player;
  auto play_pcm = [&](std::vector<int16_t> pcm) {
    if (!pcm.empty() && player) player->write(pcm);
  };
  try {
    auto payload = fields({voice_id, std::to_string(sdsherpa::rate_to_speed(rate)), text, language});
    if (!send_locked(Message::Synthesize, request, payload.data(), payload.size())) {
      std::lock_guard<std::mutex> lock(send_mutex);
      if (broker_fd >= 0) ::close(broker_fd);
      broker_fd = connect_broker();
      if (broker_fd < 0 || !send_packet(broker_fd, Message::Synthesize, request, payload.data(), payload.size()))
        throw std::runtime_error("cannot reconnect to uttermuxd");
    }
    Packet response;
    while (receive_packet(broker_fd, &response)) {
      if (response.request_id != request) continue;
      if (response.type == Message::AudioStart) {
        if (response.payload.size() != sizeof(uint32_t) && response.payload.size() != sizeof(uint32_t) + 1)
          throw std::runtime_error("invalid audio format");
        uint32_t sr; std::memcpy(&sr, response.payload.data(), sizeof(sr)); sample_rate = static_cast<int>(sr);
        if (response.payload.size() == sizeof(uint32_t) + 1) sample_format = response.payload[sizeof(uint32_t)];
        if (sample_format != 1 && sample_format != 2) throw std::runtime_error("unsupported PCM format");
        if (sample_rate < 8000 || sample_rate > 192000) throw std::runtime_error("invalid sample rate");
        processor = std::make_unique<sdsherpa::AudioProcessor>(sample_rate, pitch, volume);
        player = std::make_unique<PcmPlayer>(sample_rate);
        pending_audio.clear();
        module_report_event_begin(); began = true;
      } else if (response.type == Message::Audio) {
        if (!processor) throw std::runtime_error("PCM arrived before its format");
        pending_audio.insert(pending_audio.end(), response.payload.begin(), response.payload.end());
        const size_t sample_bytes = sample_format == 1 ? sizeof(float) : sizeof(int16_t);
        const size_t aligned_bytes = pending_audio.size() - pending_audio.size() % sample_bytes;
        if (!aligned_bytes) continue;
        if (sample_format == 1) {
          std::vector<float> samples(aligned_bytes / sizeof(float));
          std::memcpy(samples.data(), pending_audio.data(), aligned_bytes);
          play_pcm(processor->process(samples.data(), static_cast<int32_t>(samples.size()), false));
        } else {
          std::vector<int16_t> samples(aligned_bytes / sizeof(int16_t));
          std::memcpy(samples.data(), pending_audio.data(), aligned_bytes);
          std::vector<float> converted(samples.size());
          std::transform(samples.begin(), samples.end(), converted.begin(),
                         [](int16_t value) { return static_cast<float>(value) / 32768.0f; });
          play_pcm(processor->process(converted.data(), static_cast<int32_t>(converted.size()), false));
        }
        pending_audio.erase(pending_audio.begin(), pending_audio.begin() + aligned_bytes);
      } else if (response.type == Message::Done) {
        if (!pending_audio.empty()) throw std::runtime_error("incomplete final PCM sample");
        if (processor) {
          float zero = 0;
          play_pcm(processor->process(&zero, 0, true));
          player->finish();
        }
        success = true; break;
      } else if (response.type == Message::Error) {
        throw std::runtime_error(std::string(response.payload.begin(), response.payload.end()));
      }
    }
    if (paused) module_report_event_pause();
    else if (stopped || !success) module_report_event_stop();
    else { if (!began) module_report_event_begin(); module_report_event_end(); }
  } catch (const std::exception &error) {
    std::fprintf(stderr, "sd_uttermux: %s\n", error.what());
    module_report_event_stop();
  }
  current_request.store(0);
}

void join_worker() {
  if (worker.joinable() && worker.get_id() != std::this_thread::get_id()) worker.join();
}
}  // namespace

extern "C" {
int module_config(const char *) {
  try { connect_and_list(); return 0; }
  catch (const std::exception &e) { std::fprintf(stderr, "sd_uttermux: %s\n", e.what()); return -1; }
}
int module_init(char **message) {
  ::signal(SIGPIPE, SIG_IGN);
  if (message) *message = ::strdup("UtterMux broker connected");
  return voices.empty() ? -1 : 0;
}
SPDVoice **module_list_voices(void) { return speechd_voices.empty() ? nullptr : speechd_voices.data(); }
int module_speak(char *data, size_t bytes, SPDMessageType) {
  if (!data || !bytes) return 0;
  try {
    join_worker(); stopped.store(false); paused.store(false);
    std::string selected_id, language; int rate, pitch, volume;
    {
      std::lock_guard<std::mutex> lock(state_mutex);
      const auto *found = resolve_voice(current_voice);
      selected_id = found ? found->id : "";
      language = current_language;
      rate = current_rate; pitch = current_pitch; volume = current_volume;
    }
    std::string text = text_from_ssml(std::string_view(data, bytes));
    if (text.empty()) return 0;
    uint64_t request = next_request++;
    current_request.store(request);
    worker = std::thread(speak_worker, std::move(text), std::move(selected_id),
                         std::move(language), rate, pitch, volume, request);
    return 1;
  } catch (...) { current_request.store(0); return 0; }
}
int module_stop(void) {
  stopped.store(true); auto request = current_request.load();
  auto player = player_pid.load();
  if (player > 0) ::kill(player, SIGTERM);
  if (request) {
    std::lock_guard<std::mutex> lock(send_mutex);
    if (broker_fd >= 0) {
      send_packet(broker_fd, Message::Cancel, request);
      // Never leave Speech Dispatcher blocked in receive_packet() when a
      // provider or broker fails to answer cancellation. Speak reconnects.
      ::shutdown(broker_fd, SHUT_RDWR);
    }
  }
  return 0;
}
size_t module_pause(void) {
  paused.store(true);
  module_stop();
  return 0;
}
int module_close(void) {
  module_stop(); join_worker(); if (broker_fd >= 0) ::close(broker_fd); broker_fd = -1; free_voices(); return 0;
}
int module_set(const char *key, const char *value) {
  if (!key || !value) return -1;
  try {
    std::lock_guard<std::mutex> lock(state_mutex); std::string k(key), v(value);
    if (k == "rate") current_rate = std::clamp(std::stoi(v), -100, 100);
    else if (k == "pitch") current_pitch = std::clamp(std::stoi(v), -100, 100);
    else if (k == "volume") current_volume = std::clamp(std::stoi(v), -100, 100);
    else if (k == "synthesis_voice") current_voice = v == "NULL" ? "" : v;
    else if (k == "language") current_language = v == "NULL" ? "" : v;
    return 0;
  } catch (...) { return -1; }
}
int module_audio_set(const char *, const char *) { return 0; }
int module_audio_init(char **info) { if (info) *info = ::strdup("audio played through paplay"); return 0; }
int module_loglevel_set(const char *, const char *) { return 0; }
int module_debug(int, const char *) { return 0; }
int module_loop(void) { return module_process(STDIN_FILENO, 1); }
}
