#pragma once

#include "sd_sherpa/types.hpp"

#include <filesystem>
#include <string>
#include <vector>

namespace sdsherpa {

std::filesystem::path xdg_config_home();
std::filesystem::path xdg_data_home();
Settings load_settings(const std::filesystem::path &path);
Model load_model(const std::filesystem::path &path, const Settings &defaults);
std::vector<Model> load_models(const Settings &settings, std::vector<std::string> *warnings = nullptr);
VoiceRef resolve_voice(const std::vector<Model> &models, const std::string &name,
                       const std::string &language, const std::string &fallback);
bool valid_bcp47(const std::string &tag);
std::string engine_name(EngineKind engine);

}  // namespace sdsherpa
