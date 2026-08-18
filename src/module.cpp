#include "sd_sherpa/audio.hpp"
#include "sd_sherpa/config.hpp"
#include "sd_sherpa/engine.hpp"

#include <speech-dispatcher/spd_module_main.h>
#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

using namespace sdsherpa;

namespace {
Settings settings;
std::vector<Model> models;
std::unique_ptr<EngineCache> cache;
std::vector<SPDVoice *> spd_voices;
std::mutex state_mutex;
std::thread worker;
std::atomic<bool> stop_requested{false}, pause_requested{false}, speaking{false};
std::atomic<size_t> sentence_offset{0};
int current_rate = 0, current_pitch = 0, current_volume = 0;
std::string current_voice, current_language;

char *copy(const std::string &s) { return ::strdup(s.c_str()); }

void free_voices() {
  for (auto *v : spd_voices) {
    if (!v) continue;
    std::free(v->name); std::free(v->language); std::free(v->variant); std::free(v);
  }
  spd_voices.clear();
}

std::string plain_text(std::string input) {
  std::string out; out.reserve(input.size());
  bool tag = false;
  for (size_t i = 0; i < input.size(); ++i) {
    if (input[i] == '<') { tag = true; continue; }
    if (tag) { if (input[i] == '>') { tag = false; out.push_back(' '); } continue; }
    if (input.compare(i, 5, "&amp;") == 0) { out.push_back('&'); i += 4; }
    else if (input.compare(i, 4, "&lt;") == 0) { out.push_back('<'); i += 3; }
    else if (input.compare(i, 4, "&gt;") == 0) { out.push_back('>'); i += 3; }
    else if (input.compare(i, 6, "&quot;") == 0) { out.push_back('"'); i += 5; }
    else out.push_back(input[i]);
  }
  return out;
}

void join_worker() { if (worker.joinable() && worker.get_id() != std::this_thread::get_id()) worker.join(); }

void speak_worker(std::string text, VoiceRef selected, int rate, int pitch, int volume) {
  try {
    auto engine = cache->get(*selected.model);
    AudioProcessor processor(engine->sample_rate(), pitch, volume);
    auto output = [&](std::vector<int16_t> pcm) {
      if (pcm.empty()) return;
      AudioTrack track{}; track.bits = 16; track.num_channels = 1;
      track.sample_rate = engine->sample_rate();
      track.num_samples = static_cast<int>(pcm.size()); track.samples = pcm.data();
      module_tts_output_server(&track, SPD_AUDIO_LE);
    };
    const auto input = plain_text(std::move(text));
    std::vector<float> buffered;
    bool ok = !stop_requested && !pause_requested &&
      engine->generate(input, selected.voice->speaker_id, rate_to_speed(rate),
      [&](const float *samples, int32_t count, int32_t sample_rate) {
        if (stop_requested || pause_requested) return false;
        if (sample_rate != engine->sample_rate()) return false;
        buffered.insert(buffered.end(), samples, samples + count);
        return !(stop_requested || pause_requested);
      });
    if (ok && !stop_requested && !pause_requested) {
      if (buffered.size() > static_cast<size_t>(std::numeric_limits<int32_t>::max()))
        throw std::runtime_error("utterance audio exceeds supported buffer size");
      module_report_event_begin();
      if (!buffered.empty())
        output(processor.process(buffered.data(), static_cast<int32_t>(buffered.size()), true));
    }
    if (pause_requested) module_report_event_pause();
    else if (stop_requested || !ok) module_report_event_stop();
    else module_report_event_end();
  } catch (const std::exception &e) {
    std::fprintf(stderr, "sd_sherpa: synthesis failed: %s\n", e.what());
    module_report_event_stop();
  }
  speaking.store(false);
}
}  // namespace

extern "C" {

int module_config(const char *configfile) {
  try {
    std::filesystem::path p = configfile && *configfile ? configfile :
      xdg_config_home() / "speech-dispatcher/modules/sherpa.conf";
    settings = load_settings(p);
    std::vector<std::string> warnings;
    models = load_models(settings, &warnings);
    for (const auto &w : warnings) std::fprintf(stderr, "sd_sherpa: %s\n", w.c_str());
    if (models.empty()) throw std::runtime_error("no valid model manifests found in " + settings.manifests_dir.string());
    cache = std::make_unique<EngineCache>(settings.max_loaded_models);
    free_voices();
    for (const auto &m : models) for (const auto &voice : m.voice_list) {
      auto *v = static_cast<SPDVoice *>(std::calloc(1, sizeof(SPDVoice)));
      if (!v) throw std::bad_alloc();
      v->name = copy(voice.name); v->language = copy(voice.language); v->variant = copy(voice.variant);
      if (!v->name || !v->language || !v->variant) {
        std::free(v->name); std::free(v->language); std::free(v->variant); std::free(v);
        throw std::bad_alloc();
      }
      spd_voices.push_back(v);
    }
    spd_voices.push_back(nullptr);
    return 0;
  } catch (const std::exception &e) { std::fprintf(stderr, "sd_sherpa: config error: %s\n", e.what()); return -1; }
}

int module_init(char **msg) {
  try {
    module_audio_set_server();
    auto selected = resolve_voice(models, "", "", settings.default_voice);
    if (!selected.model) throw std::runtime_error("default voice cannot be resolved");
    cache->get(*selected.model);
    if (msg) *msg = ::strdup("Sherpa ONNX module initialized; default model preloaded");
    return 0;
  } catch (const std::exception &e) {
    if (msg) *msg = ::strdup(e.what());
    return -1;
  }
}

SPDVoice **module_list_voices(void) { return spd_voices.empty() ? nullptr : spd_voices.data(); }

int module_speak(char *data, size_t bytes, SPDMessageType) {
  if (!data || !bytes || models.empty()) return 0;
  try {
    join_worker(); stop_requested.store(false); pause_requested.store(false); sentence_offset.store(0);
    VoiceRef selected;
    int rate, pitch, volume;
    {
      std::lock_guard<std::mutex> lock(state_mutex);
      selected = resolve_voice(models, current_voice, current_language, settings.default_voice);
      rate = current_rate; pitch = current_pitch; volume = current_volume;
    }
    if (!selected.voice) return 0;
    speaking.store(true);
    worker = std::thread(speak_worker, std::string(data, bytes), selected, rate, pitch, volume);
    return 1;
  } catch (const std::exception &e) {
    speaking.store(false);
    std::fprintf(stderr, "sd_sherpa: cannot start synthesis: %s\n", e.what());
    return 0;
  }
}

int module_stop(void) { stop_requested.store(true); return 0; }
size_t module_pause(void) { pause_requested.store(true); return sentence_offset.load(); }

int module_close(void) {
  stop_requested.store(true); join_worker(); cache.reset(); models.clear(); free_voices(); return 0;
}

int module_set(const char *var, const char *val) {
  if (!var || !val) return -1;
  try {
    std::lock_guard<std::mutex> lock(state_mutex);
    std::string key(var), value(val);
    if (key == "rate") current_rate = std::clamp(std::stoi(value), -100, 100);
    else if (key == "pitch") current_pitch = std::clamp(std::stoi(value), -100, 100);
    else if (key == "volume") current_volume = std::clamp(std::stoi(value), -100, 100);
    else if (key == "synthesis_voice") current_voice = value == "NULL" ? "" : value;
    else if (key == "language") current_language = value == "NULL" ? "" : value;
    return 0;
  } catch (...) { return -1; }
}
int module_audio_set(const char *, const char *) { return 0; }
int module_audio_init(char **status_info) { if (status_info) *status_info = ::strdup("audio delegated to server"); return 0; }
int module_loglevel_set(const char *, const char *) { return 0; }
int module_debug(int, const char *) { return 0; }
int module_loop(void) { return module_process(STDIN_FILENO, 1); }

}  // extern C
