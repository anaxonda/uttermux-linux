#include "uttermux/protocol.hpp"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>

namespace uttermux {

namespace {
std::filesystem::path socket_path() {
  if (const char *p = std::getenv("UTTERMUX_SOCKET")) return p;
  if (const char *p = std::getenv("XDG_RUNTIME_DIR")) return std::filesystem::path(p) / "uttermux.sock";
  return std::filesystem::path("/run/user") / std::to_string(::getuid()) / "uttermux.sock";
}
}

int connect_broker() {
  int fd = ::socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
  if (fd < 0) return -1;
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  auto path = socket_path().string();
  if (path.size() >= sizeof(address.sun_path)) { ::close(fd); errno = ENAMETOOLONG; return -1; }
  std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
  if (::connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
    ::close(fd); return -1;
  }
  return fd;
}

bool send_packet(int fd, Message type, uint64_t id, const void *payload, size_t size) {
  if (size > kMaxPacket - sizeof(Header)) { errno = EMSGSIZE; return false; }
  Header h{}; h.type = static_cast<uint16_t>(type); h.request_id = id;
  h.payload_size = static_cast<uint32_t>(size);
  iovec parts[2]{{&h, sizeof(h)}, {const_cast<void *>(payload), size}};
  msghdr msg{}; msg.msg_iov = parts; msg.msg_iovlen = size ? 2 : 1;
  ssize_t sent;
  do { sent = ::sendmsg(fd, &msg, MSG_NOSIGNAL); } while (sent < 0 && errno == EINTR);
  return sent == static_cast<ssize_t>(sizeof(h) + size);
}

bool send_packet(int fd, Message type, uint64_t id, const std::string &payload) {
  return send_packet(fd, type, id, payload.data(), payload.size());
}

bool receive_packet(int fd, Packet *packet) {
  if (!packet) return false;
  std::vector<uint8_t> raw(kMaxPacket);
  ssize_t got;
  do { got = ::recv(fd, raw.data(), raw.size(), 0); } while (got < 0 && errno == EINTR);
  if (got < static_cast<ssize_t>(sizeof(Header))) return false;
  Header h{}; std::memcpy(&h, raw.data(), sizeof(h));
  if (h.magic != kMagic || h.version != kProtocolVersion ||
      h.payload_size != static_cast<uint32_t>(got - sizeof(Header))) return false;
  packet->type = static_cast<Message>(h.type); packet->request_id = h.request_id;
  packet->payload.assign(raw.begin() + sizeof(Header), raw.begin() + got);
  return true;
}

std::vector<std::string> split_fields(const std::vector<uint8_t> &payload) {
  std::vector<std::string> result;
  size_t start = 0;
  for (size_t i = 0; i <= payload.size(); ++i) if (i == payload.size() || payload[i] == 0) {
    result.emplace_back(reinterpret_cast<const char *>(payload.data() + start), i - start);
    start = i + 1;
  }
  return result;
}

std::vector<uint8_t> fields(const std::vector<std::string> &values) {
  std::vector<uint8_t> out;
  for (const auto &v : values) { out.insert(out.end(), v.begin(), v.end()); out.push_back(0); }
  return out;
}

}  // namespace uttermux
