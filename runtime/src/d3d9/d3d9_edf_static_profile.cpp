// NV-DXVK start: Explicit opt-in for one validated EDF static mesh.
#include <algorithm>
#include <array>
#include <cctype>
#include <filesystem>
#include <fstream>

#include "d3d9_edf_static_profile.h"
#include "../util/log/log.h"
#include "../util/sha1/sha1.h"
#include "../util/util_env.h"
#include "../util/util_string.h"

namespace dxvk {
  bool EdfStaticProfile::matchExecutable() {
    if (!enabled()) {
      return false;
    }

    auto lower = [](std::string value) {
      std::transform(value.begin(), value.end(), value.begin(),
        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
      return value;
    };
    if (lower(env::getExeName()) != lower(executable())) {
      Logger::info("[EDF-Static] profile_rejected reason=executable_name");
      return false;
    }

    std::ifstream file(std::filesystem::path(str::tows(env::getExePath().c_str())), std::ios::binary);
    SHA1_CTX hash;
    SHA1Init(&hash);
    std::array<uint8_t, 65536> bytes;
    while (file.read(reinterpret_cast<char*>(bytes.data()), bytes.size()) || file.gcount()) {
      SHA1Update(&hash, bytes.data(), static_cast<size_t>(file.gcount()));
    }
    if (!file.eof() || file.bad()) {
      Logger::info("[EDF-Static] profile_rejected reason=executable_read");
      return false;
    }
    uint8_t digest[SHA1_DIGEST_LENGTH];
    SHA1Final(digest, &hash);
    std::string actual;
    for (uint8_t byte : digest) {
      actual += "0123456789abcdef"[byte >> 4];
      actual += "0123456789abcdef"[byte & 15];
    }
    if (actual != lower(executableSha1())) {
      Logger::info(str::format("[EDF-Static] profile_rejected reason=executable_hash actual=", actual));
      return false;
    }
    Logger::info(str::format("[EDF-Static] profile_enabled executable_sha1=", actual,
      " mesh=925aa18481228b01392a574dbe28ba4e21aa1616 vertices=252 triangles=144 preserve_raster=true"));
    return true;
  }
}
// NV-DXVK end
