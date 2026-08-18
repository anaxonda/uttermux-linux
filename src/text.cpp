#include "uttermux/text.hpp"

#include <cctype>
#include <cstdlib>
#include <string>
#include <utility>

namespace uttermux {
namespace {
bool looks_like_tag(std::string_view input, size_t offset) {
  if (offset + 1 >= input.size()) return false;
  unsigned char next = static_cast<unsigned char>(input[offset + 1]);
  return std::isalpha(next) || next == '/' || next == '!' || next == '?';
}

bool append_entity(std::string_view entity, std::string &output) {
  if (entity == "amp") output += '&';
  else if (entity == "lt") output += '<';
  else if (entity == "gt") output += '>';
  else if (entity == "quot") output += '"';
  else if (entity == "apos") output += '\'';
  else if (entity.size() > 1 && entity[0] == '#') {
    char *end = nullptr;
    const std::string value(entity.substr(entity[1] == 'x' || entity[1] == 'X' ? 2 : 1));
    unsigned long code = std::strtoul(value.c_str(), &end,
        entity[1] == 'x' || entity[1] == 'X' ? 16 : 10);
    if (!end || *end || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return false;
    if (code <= 0x7f) output += static_cast<char>(code);
    else if (code <= 0x7ff) {
      output += static_cast<char>(0xc0 | code >> 6); output += static_cast<char>(0x80 | (code & 0x3f));
    } else if (code <= 0xffff) {
      output += static_cast<char>(0xe0 | code >> 12); output += static_cast<char>(0x80 | ((code >> 6) & 0x3f));
      output += static_cast<char>(0x80 | (code & 0x3f));
    } else {
      output += static_cast<char>(0xf0 | code >> 18); output += static_cast<char>(0x80 | ((code >> 12) & 0x3f));
      output += static_cast<char>(0x80 | ((code >> 6) & 0x3f)); output += static_cast<char>(0x80 | (code & 0x3f));
    }
  } else return false;
  return true;
}
}  // namespace

std::string text_from_ssml(std::string_view input) {
  // Speech Dispatcher may XML-escape SSML before passing it to an output
  // module. Decode first so an escaped closing tag does not become literal
  // "slash speak" after the tag-removal pass.
  std::string decoded(input);
  // SSIP/XML layers can each escape the same payload. Three bounded passes
  // handle the real double-escaped path without accepting an unbounded entity
  // expansion.
  for (int pass = 0; pass < 3; ++pass) {
    std::string next;
    next.reserve(decoded.size());
    for (size_t i = 0; i < decoded.size();) {
      if (decoded[i] == '&') {
        size_t end = decoded.find(';', i + 1);
        if (end != std::string::npos && append_entity(
              std::string_view(decoded).substr(i + 1, end - i - 1), next)) {
          i = end + 1;
          continue;
        }
      }
      next += decoded[i++];
    }
    if (next == decoded) break;
    decoded = std::move(next);
  }

  std::string output;
  output.reserve(decoded.size());
  for (size_t i = 0; i < decoded.size();) {
    if (decoded[i] == '<' && looks_like_tag(decoded, i)) {
      size_t end = decoded.find('>', i + 1);
      if (end != std::string_view::npos) {
        i = end + 1;
        continue;
      }
    }
    if (std::isspace(static_cast<unsigned char>(decoded[i]))) {
      if (!output.empty() && output.back() != ' ') output += ' ';
      ++i;
      continue;
    }
    output += decoded[i++];
  }
  // Avoid synthetic pauses from whitespace left around sentence-level tags.
  size_t begin = output.find_first_not_of(' ');
  if (begin == std::string::npos) return {};
  size_t end = output.find_last_not_of(' ');
  return output.substr(begin, end - begin + 1);
}
}  // namespace uttermux
