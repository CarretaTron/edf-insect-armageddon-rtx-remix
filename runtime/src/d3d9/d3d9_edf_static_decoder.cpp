// NV-DXVK start: One validated EDF static mesh prototype.
#include <algorithm>
#include <cmath>
#include <cstring>

#include "d3d9_edf_static_decoder.h"
#include "../util/sha1/sha1_util.h"

namespace dxvk {
  namespace {
    constexpr const char* kVertexShaderHash = "f098f457d9ae603bea5f84ec985d473c29cfb0c9";
    constexpr const char* kPixelShaderHash = "44b92a29eb3e5f2ee6d53f02e14fb11a01511da8";
    constexpr const char* kIndexHash = "2ecc0d2d1046f418d56ea51bbcca29579a04a937";
    constexpr const char* kStreamHashes[] = {
      "392f544f133f951dea21d8177cf549c553608827",
      "7c6fcffa660096291a371651cd73a85bfa5aa1ce",
      "f3ddbe4c53df3aea29e2cad8d305844c95c7dc4c",
      "d6b757b9a822e87a7983c8dca148d55ff813ed77",
      "f4d8a45daf8dcb614756f4bbd45a13d18e390b49",
    };
    constexpr uint32_t kStrides[] = { 4, 4, 8, 4, 4 };
    constexpr uint32_t kTypes[] = { 5, 5, 7, 5, 6 };
    constexpr uint32_t kUsages[] = { 7, 3, 0, 6, 5 };
    constexpr float kScale[] = { 0.25f, 1.f / 2048.f, 1.f / 127.f, 1.f };

    bool matchesHash(EdfStaticDrawDecoder::ByteSpan bytes, const char* pExpected) {
      return bytes.pData != nullptr && bytes.size != 0
          && Sha1Hash::compute(bytes.pData, bytes.size).toString() == pExpected;
    }

    double dot(const double (&a)[3], const double (&b)[3]) {
      return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }

    double determinant(const double (&m)[3][3]) {
      return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    }

    template<typename T>
    T readValue(const uint8_t* pData) {
      T result;
      std::memcpy(&result, pData, sizeof(result));
      return result;
    }

    void transform(const double (&p)[4], const float (*pMatrix)[4], double (&result)[4]) {
      for (uint32_t j = 0; j < 4; j++) {
        result[j] = 0;
        for (uint32_t i = 0; i < 4; i++) {
          result[j] += p[i] * pMatrix[i][j];
        }
      }
    }
  }

  bool EdfStaticDrawDecoder::decode(const Input& input, Output& output, const char*& pRejection) {
    output = {};
    pRejection = nullptr;
    auto reject = [&](const char* pReason) {
      output = {};
      pRejection = pReason;
      return false;
    };

    if (!matchesHash(input.vertexShader, kVertexShaderHash) || !matchesHash(input.pixelShader, kPixelShaderHash)) {
      return reject("shader content does not match the validated pair");
    }
    if (!input.indexed || input.primitiveType != 4 || input.primitiveCount != kIndexCount / 3
        || input.vertexCount != kVertexCount || input.indexStride != 2) {
      return reject("draw topology, counts or index format does not match the profile");
    }
    if (!input.opaqueMrtPass || input.baseTextureFormat != 894720068
        || input.baseTextureWidth != 128 || input.baseTextureHeight != 128 || input.baseTextureSrgb) {
      return reject("opaque MRT pass or base texture descriptor does not match the profile");
    }
    if (input.elementCount != input.elements.size()) {
      return reject("vertex declaration length does not match the profile");
    }
    for (uint32_t i = 0; i < input.elements.size(); i++) {
      const auto& element = input.elements[i];
      const auto& stream = input.streams[i];
      if (element.stream != i || element.offset || element.method || element.usageIndex
          || element.type != kTypes[i] || element.usage != kUsages[i]
          || stream.stride != kStrides[i] || stream.frequency != 1
          || stream.bytes.size != size_t(kVertexCount) * kStrides[i]) {
        return reject("vertex layout, frequency or used byte range does not match the profile");
      }
      if (!matchesHash(stream.bytes, kStreamHashes[i])) {
        return reject("used vertex attribute content does not match the single allowed mesh");
      }
    }
    if (input.indices.size != size_t(kIndexCount) * sizeof(uint16_t) || !matchesHash(input.indices, kIndexHash)) {
      return reject("used index content does not match the single allowed mesh");
    }
    for (uint32_t i = 0; i < 10; i++) {
      for (uint32_t j = 0; j < 4; j++) {
        if (!std::isfinite(input.vertexConstants[i][j])) {
          return reject("vertex shader constants are not finite");
        }
      }
    }
    for (uint32_t i = 0; i < 4; i++) {
      if (input.vertexConstants[9][i] != kScale[i] || input.pixelLightColor[i] != 1.f) {
        return reject("fixed point decoding scale or diffuse material factor changed");
      }
      for (uint32_t j = 0; j < 3; j++) {
        const float expected = i == j ? 1.f : 0.f;
        if (input.vertexConstants[4 + j][i] != expected) {
          return reject("static linear object basis changed");
        }
      }
    }
    if (!input.viewportWidth || !input.viewportHeight) {
      return reject("viewport is empty");
    }

    // The measured object basis is identity, with this object's translation anchored at zero.
    double columns[4][3] {};
    for (uint32_t j = 0; j < 4; j++) {
      for (uint32_t i = 0; i < 3; i++) {
        columns[j][i] = input.vertexConstants[i][j];
      }
    }
    const double fx = std::sqrt(dot(columns[0], columns[0]));
    const double fy = std::sqrt(dot(columns[1], columns[1]));
    const double wLength = std::sqrt(dot(columns[3], columns[3]));
    if (fx < 1e-6 || fy < 1e-6 || std::abs(wLength - 1.) > 2e-5
        || std::abs(fy / fx - double(input.viewportWidth) / input.viewportHeight) > 1e-3) {
      return reject("perspective scale or viewport aspect is inconsistent");
    }
    const double a = dot(columns[2], columns[3]) / dot(columns[3], columns[3]);
    double cameraClip[4] {};
    for (uint32_t j = 0; j < 4; j++) {
      cameraClip[j] = input.vertexConstants[3][j];
      for (uint32_t i = 0; i < 3; i++) {
        cameraClip[j] += double(input.vertexConstants[7][i]) * input.vertexConstants[i][j];
      }
    }
    const double b = cameraClip[2];
    if (a <= 1. || b >= 0. || std::abs(cameraClip[0]) > 0.002
        || std::abs(cameraClip[1]) > 0.002 || std::abs(cameraClip[3]) > 0.002) {
      return reject("camera-local position or finite depth projection is inconsistent");
    }
    double rotation[3][3] {};
    double viewTranslation[3] {};
    for (uint32_t i = 0; i < 3; i++) {
      if (std::abs(columns[2][i] - a * columns[3][i]) > 2e-5) {
        return reject("depth and clip-w columns are not parallel");
      }
      rotation[i][0] = columns[0][i] / fx;
      rotation[i][1] = columns[1][i] / fy;
      rotation[i][2] = -columns[3][i];
    }
    for (uint32_t i = 0; i < 3; i++) {
      for (uint32_t j = 0; j < 3; j++) {
        double value = 0;
        for (uint32_t k = 0; k < 3; k++) {
          value += rotation[k][i] * rotation[k][j];
        }
        if (std::abs(value - double(i == j)) > 2e-5) {
          return reject("view basis is not an orthogonal rotation");
        }
      }
    }
    if (std::abs(determinant(rotation) - 1.) > 2e-5) {
      return reject("view basis does not fit the validated proper RH rotation");
    }
    for (uint32_t i = 0; i < 4; i++) {
      output.objectToWorld[i][i] = 1.f;
    }
    for (uint32_t i = 0; i < 3; i++) {
      output.cameraAnchor[i] = input.vertexConstants[7][i];
      for (uint32_t j = 0; j < 3; j++) {
        output.worldToView[i][j] = float(rotation[i][j]);
        viewTranslation[j] -= double(input.vertexConstants[7][i]) * rotation[i][j];
      }
    }
    for (uint32_t j = 0; j < 3; j++) {
      output.worldToView[3][j] = float(viewTranslation[j]);
    }
    output.worldToView[3][3] = 1.f;
    output.viewToProjection[0][0] = float(fx);
    output.viewToProjection[1][1] = float(fy);
    output.viewToProjection[2][2] = float(-a);
    output.viewToProjection[2][3] = -1.f;
    output.viewToProjection[3][2] = float(b);

    output.vertices.resize(kVertexCount);
    for (uint32_t i = 0; i < kVertexCount; i++) {
      auto& vertex = output.vertices[i];
      for (uint32_t j = 0; j < 3; j++) {
        vertex.position[j] = readValue<int16_t>(input.streams[2].bytes.pData + size_t(i) * 8 + j * 2) * kScale[0];
        vertex.normal[j] = input.streams[1].bytes.pData[size_t(i) * 4 + j] * kScale[2] - kScale[3];
      }
      for (uint32_t j = 0; j < 2; j++) {
        vertex.uv[j] = readValue<int16_t>(input.streams[4].bytes.pData + size_t(i) * 4 + j * 2) * kScale[1];
      }
      double local[4] = { vertex.position[0], vertex.position[1], vertex.position[2], 1. };
      double expectedClip[4] {}, view[4] {}, fittedClip[4] {};
      transform(local, input.vertexConstants, expectedClip);
      transform(local, output.worldToView, view);
      transform(view, output.viewToProjection, fittedClip);
      if (std::abs(expectedClip[3]) < 1e-5 || std::abs(fittedClip[3]) < 1e-5) {
        return reject("selected mesh intersects a numerically unstable clip-w plane");
      }
      const double errorX = std::abs(expectedClip[0] / expectedClip[3] - fittedClip[0] / fittedClip[3]) * input.viewportWidth * 0.5;
      const double errorY = std::abs(expectedClip[1] / expectedClip[3] - fittedClip[1] / fittedClip[3]) * input.viewportHeight * 0.5;
      output.maxProjectionErrorPixels = std::max(output.maxProjectionErrorPixels, std::max(errorX, errorY));
      if (!std::isfinite(output.maxProjectionErrorPixels) || output.maxProjectionErrorPixels > 0.01) {
        return reject("staged float camera does not reproduce the measured shader projection");
      }
    }
    output.indices.resize(kIndexCount);
    for (uint32_t i = 0; i < kIndexCount; i++) {
      const uint16_t index = readValue<uint16_t>(input.indices.pData + size_t(i) * sizeof(uint16_t));
      if (index < 630 || index > 881) {
        return reject("index leaves the validated used vertex interval");
      }
      output.indices[i] = index - 630;
    }
    return true;
  }
}
// NV-DXVK end
