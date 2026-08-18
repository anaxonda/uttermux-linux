#include "sd_sherpa/audio.hpp"

#include <rubberband/RubberBandStretcher.h>

#include <algorithm>
#include <cmath>
#include <limits>

namespace sdsherpa {

float rate_to_speed(int rate) {
  return std::pow(2.0f, static_cast<float>(std::clamp(rate, -100, 100)) / 100.0f);
}
float pitch_to_scale(int pitch) {
  return std::pow(2.0f, static_cast<float>(std::clamp(pitch, -100, 100)) / 100.0f);
}
float volume_to_gain(int volume) {
  return std::max(0.0f, 1.0f + static_cast<float>(std::clamp(volume, -100, 100)) / 100.0f);
}

struct AudioProcessor::Impl {
  explicit Impl(int sr, float pitch)
      : stretcher(sr, 1,
          RubberBand::RubberBandStretcher::OptionProcessRealTime |
          RubberBand::RubberBandStretcher::OptionPitchHighConsistency,
          1.0, pitch) {}
  RubberBand::RubberBandStretcher stretcher;
};

AudioProcessor::AudioProcessor(int sr, int pitch, int volume)
    : sample_rate_(sr), pitch_scale_(pitch_to_scale(pitch)), gain_(volume_to_gain(volume)) {
  if (std::abs(pitch_scale_ - 1.0f) > 0.0001f) impl_ = std::make_unique<Impl>(sr, pitch_scale_);
}
AudioProcessor::~AudioProcessor() = default;
AudioProcessor::AudioProcessor(AudioProcessor&&) noexcept = default;
AudioProcessor& AudioProcessor::operator=(AudioProcessor&&) noexcept = default;

std::vector<int16_t> AudioProcessor::process(const float *samples, int32_t count, bool final) {
  std::vector<float> work;
  if (!impl_) work.assign(samples, samples + count);
  else {
    const float *channels[] = {samples};
    impl_->stretcher.process(channels, static_cast<size_t>(count), final);
    while (true) {
      int available = impl_->stretcher.available();
      if (available <= 0) break;
      size_t old = work.size(); work.resize(old + static_cast<size_t>(available));
      float *outputs[] = {work.data() + old};
      impl_->stretcher.retrieve(outputs, static_cast<size_t>(available));
    }
  }
  std::vector<int16_t> out; out.reserve(work.size());
  for (float sample : work) {
    float v = std::clamp(sample * gain_, -1.0f, 1.0f);
    out.push_back(static_cast<int16_t>(std::lrint(v * 32767.0f)));
  }
  return out;
}

}  // namespace sdsherpa
