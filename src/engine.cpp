#include "sd_sherpa/engine.hpp"

#include <dlfcn.h>

#include <cstring>
#include <exception>
#include <filesystem>
#include <stdexcept>
#include <utility>
#include <vector>

namespace sdsherpa {
namespace {

/* The TTS portion of sherpa-onnx's stable C ABI, pinned to 1.13.6. Keeping it
 * here lets sd_sherpa build before the runtime package is installed and gives
 * users a useful diagnostic instead of a loader failure at module discovery. */
struct VitsConfig { const char *model, *lexicon, *tokens, *data_dir; float noise_scale, noise_scale_w, length_scale; const char *dict_dir; };
struct MatchaConfig { const char *acoustic_model, *vocoder, *lexicon, *tokens, *data_dir; float noise_scale, length_scale; const char *dict_dir; };
struct KokoroConfig { const char *model, *voices, *tokens, *data_dir; float length_scale; const char *dict_dir, *lexicon, *lang; };
struct KittenConfig { const char *model, *voices, *tokens, *data_dir; float length_scale; };
struct ZipvoiceConfig { const char *tokens, *encoder, *decoder, *vocoder, *data_dir, *lexicon; float feat_scale, t_shift, target_rms, guidance_scale; };
struct PocketConfig { const char *lm_flow, *lm_main, *encoder, *decoder, *text_conditioner, *vocab_json, *token_scores_json; int32_t voice_embedding_cache_capacity; };
struct SupertonicConfig { const char *duration_predictor, *text_encoder, *vector_estimator, *vocoder, *tts_json, *unicode_indexer, *voice_style; };
struct ModelConfig {
  VitsConfig vits; int32_t num_threads, debug; const char *provider;
  MatchaConfig matcha; KokoroConfig kokoro; KittenConfig kitten;
  ZipvoiceConfig zipvoice; PocketConfig pocket; SupertonicConfig supertonic;
};
struct TtsConfig { ModelConfig model; const char *rule_fsts; int32_t max_num_sentences; const char *rule_fars; float silence_scale; };
struct GenerationConfig { float silence_scale, speed; int32_t sid; const float *reference_audio; int32_t reference_audio_len, reference_sample_rate; const char *reference_text; int32_t num_steps; const char *extra; };
struct GeneratedAudio { const float *samples; int32_t n, sample_rate; };
struct OfflineTts;
using Progress = int32_t (*)(const float *, int32_t, float, void *);

class Api {
 public:
  using Create = const OfflineTts *(*)(const TtsConfig *);
  using Destroy = void (*)(const OfflineTts *);
  using SampleRate = int32_t (*)(const OfflineTts *);
  using Generate = const GeneratedAudio *(*)(const OfflineTts *, const char *, const GenerationConfig *, Progress, void *);
  using DestroyAudio = void (*)(const GeneratedAudio *);

  Api() {
    const char *names[] = {"libsherpa-onnx-c-api.so", "libsherpa-onnx-c-api.so.1", nullptr};
    for (int i = 0; names[i] && !handle_; ++i) handle_ = dlopen(names[i], RTLD_NOW | RTLD_LOCAL);
    if (!handle_) throw std::runtime_error(std::string("cannot load libsherpa-onnx-c-api.so: ") + dlerror());
    create = symbol<Create>("SherpaOnnxCreateOfflineTts");
    destroy = symbol<Destroy>("SherpaOnnxDestroyOfflineTts");
    sample_rate = symbol<SampleRate>("SherpaOnnxOfflineTtsSampleRate");
    generate = symbol<Generate>("SherpaOnnxOfflineTtsGenerateWithConfig");
    destroy_audio = symbol<DestroyAudio>("SherpaOnnxDestroyOfflineTtsGeneratedAudio");
  }
  ~Api() { if (handle_) dlclose(handle_); }
  Api(const Api&) = delete; Api& operator=(const Api&) = delete;
  Create create{}; Destroy destroy{}; SampleRate sample_rate{}; Generate generate{}; DestroyAudio destroy_audio{};
 private:
  template<class T> T symbol(const char *name) {
    void *p = dlsym(handle_, name); if (!p) throw std::runtime_error(std::string("missing sherpa symbol: ") + name);
    return reinterpret_cast<T>(p);
  }
  void *handle_ = nullptr;
};

struct CallbackState {
  const AudioCallback *callback;
  int32_t sample_rate;
  bool continued = true;
  std::exception_ptr error;
};
int32_t progress_callback(const float *samples, int32_t n, float, void *opaque) {
  auto *state = static_cast<CallbackState *>(opaque);
  try {
    state->continued = (*state->callback)(samples, n, state->sample_rate);
  } catch (...) {
    state->continued = false;
    state->error = std::current_exception();
  }
  return state->continued ? 1 : 0;
}

class SherpaEngine final : public Engine {
 public:
  explicit SherpaEngine(const Model &m) : api_(std::make_shared<Api>()), model_(m) {
    TtsConfig c{};
    std::string model = m.model.string(), voices = m.voices.string(), tokens = m.tokens.string();
    std::string data = m.data_dir.string(), lexicon = m.lexicon.string(), provider = m.provider;
    std::string fsts;
    for (const auto &p : m.rule_fsts) { if (!fsts.empty()) fsts += ','; fsts += p.string(); }
    c.model.num_threads = m.num_threads; c.model.provider = provider.c_str();
    c.max_num_sentences = 1; c.silence_scale = 0.2f; c.rule_fsts = fsts.c_str();
    if (m.engine == EngineKind::Kokoro) c.model.kokoro = {model.c_str(), voices.c_str(), tokens.c_str(), data.c_str(), m.length_scale, "", lexicon.c_str(), ""};
    else if (m.engine == EngineKind::Kitten) c.model.kitten = {model.c_str(), voices.c_str(), tokens.c_str(), data.c_str(), m.length_scale};
    else c.model.vits = {model.c_str(), lexicon.c_str(), tokens.c_str(), data.c_str(), m.noise_scale, m.noise_scale_w, m.length_scale, ""};
    tts_ = api_->create(&c);
    if (!tts_) throw std::runtime_error("sherpa rejected model configuration: " + m.id);
    sample_rate_ = api_->sample_rate(tts_);
    if (sample_rate_ <= 0) throw std::runtime_error("sherpa returned an invalid sample rate");
  }
  ~SherpaEngine() override { if (tts_) api_->destroy(tts_); }
  int sample_rate() const override { return sample_rate_; }
  bool generate(const std::string &text, int32_t speaker, float speed, const AudioCallback &callback) override {
    GenerationConfig c{}; c.speed = speed; c.sid = speaker; c.silence_scale = 0.2f;
    CallbackState state{&callback, sample_rate_, true, {}};
    const GeneratedAudio *audio = api_->generate(tts_, text.c_str(), &c, progress_callback, &state);
    if (!audio) return false;
    api_->destroy_audio(audio);
    if (state.error) std::rethrow_exception(state.error);
    return state.continued;
  }
 private:
  std::shared_ptr<Api> api_;
  Model model_;
  const OfflineTts *tts_ = nullptr;
  int sample_rate_ = 0;
};

}  // namespace

std::shared_ptr<Engine> create_engine(const Model &model) { return std::make_shared<SherpaEngine>(model); }

std::shared_ptr<Engine> EngineCache::get(const Model &model) {
  std::lock_guard<std::mutex> lock(mutex_);
  auto found = entries_.find(model.id);
  if (found != entries_.end()) {
    order_.erase(found->second.lru); order_.push_front(model.id); found->second.lru = order_.begin();
    return found->second.engine;
  }
  auto engine = create_engine(model);
  while (entries_.size() >= capacity_) { auto id = order_.back(); order_.pop_back(); entries_.erase(id); }
  order_.push_front(model.id); entries_.emplace(model.id, Entry{engine, order_.begin()});
  return engine;
}

size_t EngineCache::size() const { std::lock_guard<std::mutex> lock(mutex_); return entries_.size(); }

}  // namespace sdsherpa
