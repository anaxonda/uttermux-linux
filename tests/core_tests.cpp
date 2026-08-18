#include "sd_sherpa/audio.hpp"
#include "sd_sherpa/config.hpp"
#include "uttermux/text.hpp"

#include <cmath>
#include <iostream>
#include <vector>

int main() {
  int failures = 0;
  auto check = [&](bool value, const char *message) {
    if (!value) { std::cerr << "FAIL: " << message << '\n'; ++failures; }
  };
  check(sdsherpa::valid_bcp47("en-US"), "en-US should be valid");
  check(sdsherpa::valid_bcp47("zh-Hans-CN"), "zh-Hans-CN should be valid");
  check(!sdsherpa::valid_bcp47("en_US"), "underscore locale must be rejected");
  check(std::abs(sdsherpa::rate_to_speed(0) - 1.0f) < 0.001f, "neutral rate");
  check(std::abs(sdsherpa::rate_to_speed(100) - 2.0f) < 0.001f, "maximum rate");
  check(std::abs(sdsherpa::volume_to_gain(-100)) < 0.001f, "mute volume");
  check(std::abs(sdsherpa::pitch_to_scale(100) - 2.0f) < 0.001f, "maximum pitch");
  sdsherpa::Model model;
  model.voice_list = {{"us", "US Voice", "en-US", "test", 0},
                      {"gb", "GB Voice", "en-GB", "test", 1}};
  std::vector<sdsherpa::Model> models{model};
  auto selected = sdsherpa::resolve_voice(models, "", "en-GB", "US Voice");
  check(selected.voice && selected.voice->name == "GB Voice", "requested language precedes default voice");
  check(uttermux::text_from_ssml("<speak>Hello world.</speak>") == "Hello world.",
        "sentence SSML wrapper is removed");
  check(uttermux::text_from_ssml("&lt;speak&gt;Hello world.&lt;/speak&gt;") == "Hello world.",
        "Speech Dispatcher escaped SSML wrapper is removed");
  check(uttermux::text_from_ssml("&amp;lt;speak&amp;gt;Hello world.&amp;lt;/speak&amp;gt;") == "Hello world.",
        "double-escaped SSIP SSML wrapper is removed");
  check(uttermux::text_from_ssml("<speak>A &amp; B <break time=\"1s\"/> C.</speak>") == "A & B C.",
        "SSML tags are removed and entities decoded");
  check(uttermux::text_from_ssml("one < two") == "one < two", "literal less-than is retained");
  std::vector<float> tone(2400);
  for (size_t i = 0; i < tone.size(); ++i)
    tone[i] = static_cast<float>(0.25 * std::sin(2.0 * 3.141592653589793 * 440.0 * static_cast<double>(i) / 24000.0));
  sdsherpa::AudioProcessor pitched(24000, 25, 0);
  auto shifted = pitched.process(tone.data(), static_cast<int32_t>(tone.size()), true);
  check(!shifted.empty(), "pitch processor flushes output");
  return failures ? 1 : 0;
}
