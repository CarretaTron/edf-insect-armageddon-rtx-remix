// Standalone evidence check using the actual Remix matrix/projection helpers.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include "rtx_render/rtx_matrix_helpers.h"

int main(int argc, char** argv) {
  if (argc != 2) { return 2; }
  std::ifstream input(argv[1], std::ios::binary);
  auto read = [&](void* pData, size_t bytes) {
    input.read(static_cast<char*>(pData), bytes);
    return input.good();
  };
  uint32_t count = 0;
  if (!read(&count, sizeof(count))) { return 2; }
  if (count == 0 || count > 64) { return 2; }
  std::cout << "[";
  bool valid = true;
  for (uint32_t i = 0; i < count; ++i) {
    uint32_t frame = 0, vertices = 0;
    float raw[4][4], w[4][4], v[4][4], p[4][4];
    if (!read(&frame, 4) || !read(&vertices, 4) || !read(raw, sizeof(raw))
        || !read(w, sizeof(w)) || !read(v, sizeof(v)) || !read(p, sizeof(p))) { return 2; }
    if (vertices == 0 || vertices > 100000) { return 2; }
    // D3D row vectors become columns in Matrix4. Internal composition is P*V*W.
    dxvk::Matrix4 original(raw), world(w), view(v), projection(p);
    dxvk::Matrix4 fitted = projection * view * world;
    DecomposeProjectionParams parameters;
    decomposeProjection(projection, parameters);
    double maxPixel = 0;
    for (uint32_t j = 0; j < vertices; ++j) {
      float xyz[3];
      if (!read(xyz, sizeof(xyz))) { return 2; }
      dxvk::Vector4 position(xyz[0], xyz[1], xyz[2], 1);
      dxvk::Vector4 a = original * position, b = fitted * position;
      if (a.w <= 0 || b.w <= 0) { return 1; }
      maxPixel = std::max(maxPixel, std::abs(double(a.x) / a.w - double(b.x) / b.w) * 960);
      maxPixel = std::max(maxPixel, std::abs(double(a.y) / a.w - double(b.y) / b.w) * 540);
    }
    const bool caseValid = std::isfinite(parameters.fov) && parameters.fov > 0
      && std::abs(parameters.shearX) < 0.01f && !parameters.isLHS && !parameters.isReverseZ
      && parameters.nearPlane > 0 && parameters.farPlane > parameters.nearPlane && maxPixel < 0.005;
    valid &= caseValid;
    if (i) { std::cout << ","; }
    std::cout << "{\"frame\":" << frame << ",\"valid\":" << (caseValid ? "true" : "false")
      << ",\"matrix4ProjectionMaxPixelError\":" << maxPixel
      << ",\"aspect\":" << parameters.aspectRatio << ",\"fovRadians\":" << parameters.fov
      << ",\"nearPlane\":" << parameters.nearPlane << ",\"farPlane\":" << parameters.farPlane
      << ",\"shearX\":" << parameters.shearX << ",\"shearY\":" << parameters.shearY
      << ",\"leftHanded\":" << (parameters.isLHS ? "true" : "false")
      << ",\"reverseZ\":" << (parameters.isReverseZ ? "true" : "false") << "}";
  }
  if (input.peek() != std::char_traits<char>::eof()) { return 2; }
  std::cout << "]\n";
  return valid ? 0 : 1;
}
