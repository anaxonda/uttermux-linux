#pragma once

#include "sd_sherpa/types.hpp"

#include <atomic>
#include <functional>
#include <list>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

namespace sdsherpa {

using AudioCallback = std::function<bool(const float *, int32_t, int32_t)>;

class Engine {
 public:
  virtual ~Engine() = default;
  virtual int sample_rate() const = 0;
  virtual bool generate(const std::string &text, int32_t speaker, float speed,
                        const AudioCallback &callback) = 0;
};

std::shared_ptr<Engine> create_engine(const Model &model);

class EngineCache {
 public:
  explicit EngineCache(size_t capacity) : capacity_(capacity ? capacity : 1) {}
  std::shared_ptr<Engine> get(const Model &model);
  size_t size() const;

 private:
  struct Entry { std::shared_ptr<Engine> engine; std::list<std::string>::iterator lru; };
  size_t capacity_;
  mutable std::mutex mutex_;
  std::list<std::string> order_;
  std::unordered_map<std::string, Entry> entries_;
};

}  // namespace sdsherpa
