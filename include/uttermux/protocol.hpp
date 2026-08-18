#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace uttermux {

constexpr uint32_t kMagic = 0x58544d55;  // UMTX, little endian
constexpr uint16_t kProtocolVersion = 1;
constexpr size_t kMaxPacket = 64 * 1024;

enum class Message : uint16_t {
  Hello = 1, ListVoices = 2, Voice = 3, Synthesize = 4, AudioStart = 5,
  Audio = 6, Done = 7, Cancel = 8, Error = 9, Health = 10,
};

#pragma pack(push, 1)
struct Header {
  uint32_t magic = kMagic;
  uint16_t version = kProtocolVersion;
  uint16_t type = 0;
  uint64_t request_id = 0;
  uint32_t payload_size = 0;
};
#pragma pack(pop)

struct Packet {
  Message type{};
  uint64_t request_id = 0;
  std::vector<uint8_t> payload;
};

int connect_broker();
bool send_packet(int fd, Message type, uint64_t request_id,
                 const void *payload = nullptr, size_t payload_size = 0);
bool send_packet(int fd, Message type, uint64_t request_id, const std::string &payload);
bool receive_packet(int fd, Packet *packet);
std::vector<std::string> split_fields(const std::vector<uint8_t> &payload);
std::vector<uint8_t> fields(const std::vector<std::string> &values);

}  // namespace uttermux
