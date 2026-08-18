#include "sd_sherpa/config.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>

namespace fs = std::filesystem;

namespace sdsherpa {
namespace {

std::string trim(std::string s) {
  auto ws = [](unsigned char c) { return std::isspace(c); };
  s.erase(s.begin(), std::find_if_not(s.begin(), s.end(), ws));
  s.erase(std::find_if_not(s.rbegin(), s.rend(), ws).base(), s.end());
  return s;
}

std::string unquote(const std::string &value) {
  std::string v = trim(value);
  if (v.size() < 2 || v.front() != '"' || v.back() != '"')
    throw std::runtime_error("expected quoted TOML string: " + value);
  std::string out;
  for (size_t i = 1; i + 1 < v.size(); ++i) {
    if (v[i] == '\\' && i + 2 < v.size()) {
      char n = v[++i];
      if (n == 'n') out.push_back('\n');
      else if (n == 't') out.push_back('\t');
      else if (n == '"' || n == '\\') out.push_back(n);
      else throw std::runtime_error("unsupported TOML escape");
    } else out.push_back(v[i]);
  }
  return out;
}

std::vector<std::string> string_array(const std::string &value) {
  std::string v = trim(value);
  if (v.size() < 2 || v.front() != '[' || v.back() != ']')
    throw std::runtime_error("expected TOML string array");
  std::vector<std::string> result;
  std::string item;
  bool quoted = false, escaped = false;
  for (size_t i = 1; i + 1 < v.size(); ++i) {
    char c = v[i];
    if (escaped) { item.push_back(c); escaped = false; continue; }
    if (c == '\\' && quoted) { item.push_back(c); escaped = true; continue; }
    if (c == '"') { quoted = !quoted; item.push_back(c); continue; }
    if (c == ',' && !quoted) { if (!trim(item).empty()) result.push_back(unquote(item)); item.clear(); }
    else item.push_back(c);
  }
  if (quoted) throw std::runtime_error("unterminated TOML string array");
  if (!trim(item).empty()) result.push_back(unquote(item));
  return result;
}

using Sections = std::vector<std::pair<std::string, std::map<std::string, std::string>>>;

Sections parse(const fs::path &path) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error("cannot open " + path.string());
  Sections sections{{"", {}}};
  std::string line;
  size_t number = 0;
  while (std::getline(in, line)) {
    ++number;
    bool quoted = false, escaped = false;
    size_t comment = std::string::npos;
    for (size_t i = 0; i < line.size(); ++i) {
      if (escaped) { escaped = false; continue; }
      if (line[i] == '\\' && quoted) { escaped = true; continue; }
      if (line[i] == '"') quoted = !quoted;
      if (line[i] == '#' && !quoted) { comment = i; break; }
    }
    if (comment != std::string::npos) line.erase(comment);
    line = trim(line);
    if (line.empty()) continue;
    if (line.rfind("[[", 0) == 0 && line.size() > 4 && line.substr(line.size()-2) == "]]" ) {
      sections.push_back({trim(line.substr(2, line.size()-4)), {}});
      continue;
    }
    if (line.front() == '[' && line.back() == ']') {
      sections.push_back({trim(line.substr(1, line.size()-2)), {}});
      continue;
    }
    auto eq = line.find('=');
    if (eq == std::string::npos) throw std::runtime_error(path.string() + ":" + std::to_string(number) + ": expected key=value");
    auto key = trim(line.substr(0, eq));
    if (key.empty() || sections.back().second.count(key)) throw std::runtime_error("duplicate or empty TOML key: " + key);
    sections.back().second[key] = trim(line.substr(eq + 1));
  }
  return sections;
}

std::string str(const std::map<std::string,std::string> &m, const std::string &k,
                const std::string &fallback = {}) {
  auto it = m.find(k); return it == m.end() ? fallback : unquote(it->second);
}
int integer(const std::map<std::string,std::string> &m, const std::string &k, int fallback) {
  auto it = m.find(k);
  if (it == m.end()) return fallback;
  size_t used = 0;
  int result = std::stoi(it->second, &used);
  if (!trim(it->second.substr(used)).empty()) throw std::runtime_error("invalid integer for " + k);
  return result;
}
float real(const std::map<std::string,std::string> &m, const std::string &k, float fallback) {
  auto it = m.find(k);
  if (it == m.end()) return fallback;
  size_t used = 0;
  float result = std::stof(it->second, &used);
  if (!trim(it->second.substr(used)).empty() || !std::isfinite(result))
    throw std::runtime_error("invalid real number for " + k);
  return result;
}

fs::path expand_path(std::string value, const fs::path &base) {
  if (value.rfind("~/", 0) == 0) {
    const char *home = std::getenv("HOME");
    if (!home) throw std::runtime_error("HOME is not set");
    value = (fs::path(home) / value.substr(2)).string();
  }
  fs::path p(value);
  if (p.is_relative()) p = base / p;
  return p.lexically_normal();
}

void require_file(const fs::path &path, const std::string &field) {
  if (path.empty() || !fs::is_regular_file(path)) throw std::runtime_error(field + " is not a regular file: " + path.string());
}

}  // namespace

fs::path xdg_config_home() {
  if (const char *p = std::getenv("XDG_CONFIG_HOME")) return p;
  if (const char *p = std::getenv("HOME")) return fs::path(p) / ".config";
  throw std::runtime_error("neither XDG_CONFIG_HOME nor HOME is set");
}

fs::path xdg_data_home() {
  if (const char *p = std::getenv("XDG_DATA_HOME")) return p;
  if (const char *p = std::getenv("HOME")) return fs::path(p) / ".local/share";
  throw std::runtime_error("neither XDG_DATA_HOME nor HOME is set");
}

bool valid_bcp47(const std::string &tag) {
  static const std::regex re("^[A-Za-z]{2,3}(-[A-Za-z]{4})?(-([A-Za-z]{2}|[0-9]{3}))?(-[A-Za-z0-9]{5,8})*$");
  return std::regex_match(tag, re);
}

std::string engine_name(EngineKind e) {
  if (e == EngineKind::Kokoro) return "kokoro";
  if (e == EngineKind::Kitten) return "kitten";
  return "vits";
}

Settings load_settings(const fs::path &path) {
  Settings s;
  s.manifests_dir = xdg_config_home() / "speech-dispatcher-sherpa/models.d";
  if (!fs::exists(path)) return s;
  auto sections = parse(path);
  const auto &m = sections.front().second;
  s.schema_version = integer(m, "schema_version", 1);
  s.default_voice = str(m, "default_voice", s.default_voice);
  s.max_loaded_models = static_cast<size_t>(integer(m, "max_loaded_models", 2));
  s.num_threads = integer(m, "num_threads", 4);
  s.provider = str(m, "provider", "cpu");
  auto dir = str(m, "manifests_dir");
  if (!dir.empty()) s.manifests_dir = expand_path(dir, path.parent_path());
  if (s.schema_version != 1) throw std::runtime_error("unsupported settings schema_version");
  if (s.provider != "cpu") throw std::runtime_error("v1 supports only provider=cpu");
  if (s.max_loaded_models < 1 || s.max_loaded_models > 16) throw std::runtime_error("max_loaded_models must be 1..16");
  if (s.num_threads < 1 || s.num_threads > 128) throw std::runtime_error("num_threads must be 1..128");
  return s;
}

Model load_model(const fs::path &path, const Settings &defaults) {
  auto sections = parse(path);
  Model model;
  model.num_threads = defaults.num_threads;
  model.provider = defaults.provider;
  const auto &top = sections.front().second;
  model.schema_version = integer(top, "schema_version", 1);
  model.id = str(top, "id");
  auto engine = str(top, "engine");
  if (engine == "kokoro") model.engine = EngineKind::Kokoro;
  else if (engine == "kitten") model.engine = EngineKind::Kitten;
  else if (engine == "vits" || engine == "piper") model.engine = EngineKind::Vits;
  else throw std::runtime_error("unsupported engine: " + engine);
  model.root = expand_path(str(top, "root"), path.parent_path());
  model.provider = str(top, "provider", model.provider);
  model.num_threads = integer(top, "num_threads", model.num_threads);
  model.length_scale = real(top, "length_scale", 1.0f);
  model.noise_scale = real(top, "noise_scale", 0.667f);
  model.noise_scale_w = real(top, "noise_scale_w", 0.8f);
  if (model.schema_version != 1 || model.id.empty()) throw std::runtime_error("model requires schema_version=1 and a non-empty id");
  if (model.provider != "cpu") throw std::runtime_error("v1 supports only provider=cpu");
  if (model.num_threads < 1 || model.num_threads > 128) throw std::runtime_error("num_threads must be 1..128");
  if (model.length_scale <= 0.0f || model.length_scale > 10.0f) throw std::runtime_error("length_scale must be in (0, 10]");
  if (model.noise_scale < 0.0f || model.noise_scale_w < 0.0f) throw std::runtime_error("noise scales cannot be negative");

  bool saw_files = false;
  for (size_t i = 1; i < sections.size(); ++i) {
    const auto &[name, values] = sections[i];
    if (name == "files") {
      if (saw_files) throw std::runtime_error("model contains multiple [files] sections");
      saw_files = true;
      auto p = [&](const char *key) { auto v = str(values, key); return v.empty() ? fs::path{} : expand_path(v, model.root); };
      model.model = p("model"); model.voices = p("voices"); model.tokens = p("tokens");
      model.data_dir = p("data_dir"); model.lexicon = p("lexicon");
      auto it = values.find("rule_fsts");
      if (it != values.end()) for (const auto &v : string_array(it->second)) model.rule_fsts.push_back(expand_path(v, model.root));
    } else if (name == "voice") {
      Voice v;
      v.id = str(values, "id"); v.name = str(values, "name");
      v.language = str(values, "language"); v.variant = str(values, "variant", engine_name(model.engine));
      v.speaker_id = integer(values, "speaker_id", 0);
      if (v.id.empty() || v.name.empty() || !valid_bcp47(v.language) || v.speaker_id < 0)
        throw std::runtime_error("invalid voice entry in " + path.string());
      model.voice_list.push_back(std::move(v));
    } else throw std::runtime_error("unknown TOML section: " + name);
  }
  require_file(model.model, "files.model");
  require_file(model.tokens, "files.tokens");
  if (model.engine != EngineKind::Vits) require_file(model.voices, "files.voices");
  if (!model.data_dir.empty() && !fs::is_directory(model.data_dir)) throw std::runtime_error("files.data_dir is not a directory");
  if (!model.lexicon.empty()) require_file(model.lexicon, "files.lexicon");
  for (const auto &p : model.rule_fsts) require_file(p, "files.rule_fsts");
  if (model.voice_list.empty()) throw std::runtime_error("model has no voices");
  std::set<std::string> names, ids;
  for (const auto &v : model.voice_list)
    if (!names.insert(v.name).second || !ids.insert(v.id).second) throw std::runtime_error("duplicate voice id or name in model " + model.id);
  return model;
}

std::vector<Model> load_models(const Settings &settings, std::vector<std::string> *warnings) {
  std::vector<Model> out;
  if (!fs::exists(settings.manifests_dir)) return out;
  std::vector<fs::path> paths;
  for (const auto &e : fs::directory_iterator(settings.manifests_dir)) if (e.path().extension() == ".toml") paths.push_back(e.path());
  std::sort(paths.begin(), paths.end());
  std::set<std::string> model_ids, voice_names;
  for (const auto &p : paths) {
    try {
      Model m = load_model(p, settings);
      if (!model_ids.insert(m.id).second) throw std::runtime_error("duplicate model id: " + m.id);
      for (const auto &v : m.voice_list) if (!voice_names.insert(v.name).second) throw std::runtime_error("duplicate global voice name: " + v.name);
      out.push_back(std::move(m));
    } catch (const std::exception &e) { if (warnings) warnings->push_back(p.string() + ": " + e.what()); }
  }
  return out;
}

VoiceRef resolve_voice(const std::vector<Model> &models, const std::string &name,
                       const std::string &language, const std::string &fallback) {
  auto find_name = [&](const std::string &n) -> VoiceRef {
    for (const auto &m : models) for (const auto &v : m.voice_list) if (v.name == n || v.id == n) return {&m, &v};
    return {};
  };
  if (!name.empty()) if (auto r = find_name(name); r.voice) return r;
  if (!language.empty()) {
    for (const auto &m : models) for (const auto &v : m.voice_list) if (v.language == language) return {&m, &v};
    auto dash = language.find('-'); auto base = language.substr(0, dash);
    for (const auto &m : models) for (const auto &v : m.voice_list) if (v.language.substr(0, v.language.find('-')) == base) return {&m, &v};
  }
  if (!fallback.empty()) if (auto r = find_name(fallback); r.voice) return r;
  if (!models.empty() && !models.front().voice_list.empty()) return {&models.front(), &models.front().voice_list.front()};
  return {};
}

}  // namespace sdsherpa
