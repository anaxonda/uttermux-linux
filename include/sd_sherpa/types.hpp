#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace sdsherpa {

enum class EngineKind { Kokoro, Kitten, Vits };

struct Voice {
  std::string id;
  std::string name;
  std::string language;
  std::string variant;
  int32_t speaker_id = 0;
};

struct Model {
  int schema_version = 1;
  std::string id;
  EngineKind engine = EngineKind::Kokoro;
  std::filesystem::path root;
  std::filesystem::path model;
  std::filesystem::path voices;
  std::filesystem::path tokens;
  std::filesystem::path data_dir;
  std::filesystem::path lexicon;
  std::vector<std::filesystem::path> rule_fsts;
  std::string provider = "cpu";
  int32_t num_threads = 4;
  float length_scale = 1.0f;
  float noise_scale = 0.667f;
  float noise_scale_w = 0.8f;
  std::vector<Voice> voice_list;
};

struct Settings {
  int schema_version = 1;
  std::string default_voice = "Kokoro Heart";
  size_t max_loaded_models = 2;
  int32_t num_threads = 4;
  std::string provider = "cpu";
  std::filesystem::path manifests_dir;
};

struct VoiceRef {
  const Model *model = nullptr;
  const Voice *voice = nullptr;
};

}  // namespace sdsherpa
