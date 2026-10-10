#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

#include "../../src/d3d9/d3d9_edf_static_decoder.h"

namespace {
  using Decoder = dxvk::EdfStaticDrawDecoder;

  void require(bool condition, const char* pMessage) {
    if (!condition) {
      throw std::runtime_error(pMessage);
    }
  }

  template<typename T>
  T read(std::ifstream& stream) {
    T result {};
    require(bool(stream.read(reinterpret_cast<char*>(&result), sizeof(result))), "Truncated fixture.");
    return result;
  }

  std::vector<uint8_t> readBytes(std::ifstream& stream) {
    const auto length = read<uint32_t>(stream);
    require(length < 1024 * 1024, "Fixture byte span is too large.");
    std::vector<uint8_t> result(length);
    require(bool(stream.read(reinterpret_cast<char*>(result.data()), length)), "Truncated fixture bytes.");
    return result;
  }

  Decoder::ByteSpan span(const std::vector<uint8_t>& bytes) {
    return { bytes.data(), bytes.size() };
  }

  void rejects(const Decoder::Input& input, const char* pTest) {
    Decoder::Output output;
    output.vertices.resize(1);
    const char* pReason = nullptr;
    require(!Decoder::decode(input, output, pReason), pTest);
    require(pReason != nullptr && output.vertices.empty() && output.indices.empty(), "Rejected output must be empty.");
  }
}

int main(int argc, char** argv) {
  try {
    require(argc == 2, "Pass the captured replay fixture path.");
    std::ifstream stream(argv[1], std::ios::binary);
    require(bool(stream), "Cannot open fixture.");
    char magic[8];
    require(bool(stream.read(magic, 8)) && std::memcmp(magic, "EDFDEC01", 8) == 0, "Incorrect fixture magic.");
    const uint32_t caseCount = read<uint32_t>(stream);
    require(caseCount == 3, "The three observed captures are required.");
    double cameras[3][3] {};
    for (uint32_t capture = 0; capture < caseCount; capture++) {
      Decoder::Input input;
      input.elementCount = read<uint32_t>(stream);
      for (auto& element : input.elements) {
        element.stream = read<uint32_t>(stream);
        element.offset = read<uint32_t>(stream);
        element.type = read<uint32_t>(stream);
        element.method = read<uint32_t>(stream);
        element.usage = read<uint32_t>(stream);
        element.usageIndex = read<uint32_t>(stream);
      }
      std::array<std::vector<uint8_t>, 5> buffers;
      for (uint32_t i = 0; i < 5; i++) {
        input.streams[i].stride = read<uint32_t>(stream);
        input.streams[i].frequency = read<uint32_t>(stream);
        buffers[i] = readBytes(stream);
        input.streams[i].bytes = span(buffers[i]);
      }
      auto vertexShader = readBytes(stream);
      auto pixelShader = readBytes(stream);
      input.vertexShader = span(vertexShader);
      input.pixelShader = span(pixelShader);
      input.indexStride = read<uint32_t>(stream);
      auto indices = readBytes(stream);
      input.indices = span(indices);
      input.vertexCount = read<uint32_t>(stream);
      input.primitiveCount = read<uint32_t>(stream);
      input.primitiveType = read<uint32_t>(stream);
      input.indexed = bool(read<uint32_t>(stream));
      input.opaqueMrtPass = bool(read<uint32_t>(stream));
      input.baseTextureFormat = read<uint32_t>(stream);
      input.baseTextureWidth = read<uint32_t>(stream);
      input.baseTextureHeight = read<uint32_t>(stream);
      input.baseTextureSrgb = bool(read<uint32_t>(stream));
      input.viewportWidth = read<uint32_t>(stream);
      input.viewportHeight = read<uint32_t>(stream);
      for (auto& row : input.vertexConstants) {
        for (auto& component : row) {
          component = read<float>(stream);
        }
      }
      for (auto& component : input.pixelLightColor) {
        component = read<float>(stream);
      }
      std::array<std::array<double, 8>, Decoder::kVertexCount> expected;
      for (auto& vertex : expected) {
        for (auto& component : vertex) {
          component = read<double>(stream);
        }
      }
      require(read<uint32_t>(stream) == Decoder::kIndexCount, "Incorrect oracle index count.");
      std::array<uint16_t, Decoder::kIndexCount> expectedIndices;
      for (auto& index : expectedIndices) {
        index = read<uint16_t>(stream);
      }
      Decoder::Output output;
      const char* pReason = nullptr;
      if (!Decoder::decode(input, output, pReason)) {
        throw std::runtime_error(pReason ? pReason : "Unexpected decoder rejection.");
      }
      require(output.vertices.size() == expected.size() && output.indices.size() == expectedIndices.size(), "Output counts differ.");
      for (uint32_t i = 0; i < output.vertices.size(); i++) {
        float actual[8];
        std::memcpy(actual, &output.vertices[i], sizeof(actual));
        for (uint32_t j = 0; j < 8; j++) {
          require(std::abs(double(actual[j]) - expected[i][j]) < 2e-7, "Decoded POSITION/NORMAL/UV differs from the independently executed shader.");
        }
      }
      for (uint32_t i = 0; i < output.indices.size(); i++) {
        require(output.indices[i] == expectedIndices[i] && output.indices[i] < output.vertices.size(), "Rebased triangle indices differ.");
      }
      require(output.maxProjectionErrorPixels < 0.001, "Captured camera projection error exceeds 0.001 pixel.");
      for (uint32_t j = 0; j < 3; j++) {
        cameras[capture][j] = output.cameraAnchor[j];
      }
      std::cout << "{\"captureIndex\":" << capture << ",\"vertices\":" << output.vertices.size()
                << ",\"indices\":" << output.indices.size() << ",\"maxProjectionErrorPixels\":"
                << output.maxProjectionErrorPixels << "}\n";

      auto invalid = input;
      invalid.streams[2].frequency = 0x40000002;
      rejects(invalid, "Instancing must be rejected.");
      invalid = input;
      invalid.vertexConstants[9][0] = 1.f / 1024.f;
      rejects(invalid, "The old mesh scale must be rejected.");
      invalid = input;
      invalid.vertexConstants[4][0] = 0.99f;
      rejects(invalid, "A changing object transform must be rejected.");
      invalid = input;
      invalid.pixelLightColor[0] = 0.5f;
      rejects(invalid, "A different material factor must be rejected.");
      invalid = input;
      invalid.baseTextureSrgb = true;
      rejects(invalid, "A different texture transfer function must be rejected.");
      invalid = input;
      invalid.opaqueMrtPass = false;
      rejects(invalid, "A different rendering pass must be rejected.");
      invalid = input;
      invalid.streams[4].bytes.size--;
      rejects(invalid, "Truncated stream must be rejected.");
      invalid = input;
      invalid.elements[4].usage = 3;
      rejects(invalid, "Different semantics must be rejected.");
      invalid = input;
      invalid.vertexConstants[7][0] += 1.f;
      rejects(invalid, "Inconsistent camera position must be rejected.");
      buffers[2][0] ^= 1;
      rejects(input, "Changed vertex content must be rejected.");
      buffers[2][0] ^= 1;
      indices[0] ^= 1;
      rejects(input, "Changed index content must be rejected.");
      indices[0] ^= 1;
      vertexShader[0] ^= 1;
      rejects(input, "Changed shader bytecode must be rejected.");
    }
    require(stream.peek() == std::char_traits<char>::eof(), "Trailing fixture bytes.");
    double stationaryDistance = 0;
    double movedDistance = 0;
    for (uint32_t i = 0; i < 3; i++) {
      stationaryDistance += std::pow(cameras[1][i] - cameras[0][i], 2);
      movedDistance += std::pow(cameras[2][i] - cameras[0][i], 2);
    }
    require(std::sqrt(stationaryDistance) < 0.0002, "Stationary capture camera drift is inconsistent.");
    require(std::sqrt(movedDistance) > 1.16 && std::sqrt(movedDistance) < 1.17, "Moved capture camera delta is inconsistent.");
    std::cout << "{\"result\":\"passed\",\"capturedCases\":3,\"negativeCases\":36}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
