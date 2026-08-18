#pragma once

#include <cstdint>
#include <memory>
#include <vector>

namespace sdsherpa {

float rate_to_speed(int rate);
float pitch_to_scale(int pitch);
float volume_to_gain(int volume);

class AudioProcessor {
 public:
  AudioProcessor(int sample_rate, int pitch, int volume);
  ~AudioProcessor();
  AudioProcessor(AudioProcessor&&) noexcept;
  AudioProcessor& operator=(AudioProcessor&&) noexcept;
  std::vector<int16_t> process(const float *samples, int32_t count, bool final);

 private:
  struct Impl;
  int sample_rate_;
  float pitch_scale_;
  float gain_;
  std::unique_ptr<Impl> impl_;
};

}  // namespace sdsherpa
