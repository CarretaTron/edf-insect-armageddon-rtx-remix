// NV-DXVK start: One validated EDF static mesh prototype.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace dxvk {

  class EdfStaticDrawDecoder {
  public:
    static constexpr const char* kProfileId = "edf-static-925aa18481228b01392a574dbe28ba4e21aa1616";
    static constexpr uint32_t kVertexCount = 252;
    static constexpr uint32_t kIndexCount = 432;

    struct ByteSpan {
      const uint8_t* pData = nullptr;
      size_t size = 0;
    };

    struct Element {
      uint32_t stream = 0;
      uint32_t offset = 0;
      uint32_t type = 0;
      uint32_t method = 0;
      uint32_t usage = 0;
      uint32_t usageIndex = 0;
    };

    struct Stream {
      // Contains exactly the used min-to-max vertex interval.
      ByteSpan bytes;
      uint32_t stride = 0;
      uint32_t frequency = 0;
    };

    struct Input {
      ByteSpan vertexShader;
      ByteSpan pixelShader;
      std::array<Element, 5> elements;
      uint32_t elementCount = 0;
      std::array<Stream, 5> streams;
      ByteSpan indices;
      uint32_t indexStride = 0;
      uint32_t vertexCount = 0;
      uint32_t primitiveCount = 0;
      uint32_t primitiveType = 0;
      bool indexed = false;
      bool opaqueMrtPass = false;
      uint32_t baseTextureFormat = 0;
      uint32_t baseTextureWidth = 0;
      uint32_t baseTextureHeight = 0;
      bool baseTextureSrgb = false;
      uint32_t viewportWidth = 0;
      uint32_t viewportHeight = 0;
      float vertexConstants[10][4] {};
      float pixelLightColor[4] {};
    };

    struct Vertex {
      float position[3];
      float normal[3];
      float uv[2];
    };
    static_assert(sizeof(Vertex) == 32, "EDF staged vertices use FLOAT3/FLOAT3/FLOAT2.");

    struct Output {
      std::vector<Vertex> vertices;
      std::vector<uint16_t> indices;
      // D3D row-vector convention. Matrix4 copies these rows into its columns.
      float objectToWorld[4][4] {};
      float worldToView[4][4] {};
      float viewToProjection[4][4] {};
      double cameraAnchor[3] {};
      double maxProjectionErrorPixels = 0;
    };

    // The adapter separately requires opt-in and the verified executable fingerprint.
    static bool decode(const Input& input, Output& output, const char*& pRejection);
  };

}
// NV-DXVK end
