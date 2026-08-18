#include "sd_sherpa/config.hpp"
#include "sd_sherpa/engine.hpp"

#include <cstdint>
#include <iostream>
#include <string>

int main(int argc, char **argv) {
  if (argc != 2) {
    std::cerr << "usage: engine_smoke MODEL_MANIFEST\n";
    return 2;
  }
  try {
    sdsherpa::Settings defaults;
    auto model = sdsherpa::load_model(argv[1], defaults);
    auto engine = sdsherpa::create_engine(model);
    int64_t samples = 0;
    bool ok = engine->generate("This is a pre-installation synthesis test.",
                               model.voice_list.front().speaker_id, 1.0f,
      [&](const float *pcm, int32_t count, int32_t sample_rate) {
        if (!pcm || count < 0 || sample_rate != engine->sample_rate()) return false;
        samples += count;
        return true;
      });
    if (!ok || samples <= 0) {
      std::cerr << "generation returned no streaming audio\n";
      return 1;
    }
    std::cout << "generated " << samples << " samples at " << engine->sample_rate() << " Hz\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "engine smoke test failed: " << e.what() << '\n';
    return 1;
  }
}
