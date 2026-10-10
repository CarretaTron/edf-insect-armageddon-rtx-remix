/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>

// NV-DXVK start: Opt-in compatibility diagnostics.
namespace dxvk {

  struct BlobRef {
    std::string hash;
    std::string path;
    uint64_t size = 0;
    bool available = false;
  };

  class CompatibilityCaptureWriter {
  public:
    explicit CompatibilityCaptureWriter(
      std::filesystem::path outputDirectory,
      uint64_t maxBytes = 256ull * 1024 * 1024,
      uint64_t maxQueueBytes = 16ull * 1024 * 1024);
    ~CompatibilityCaptureWriter();

    CompatibilityCaptureWriter(const CompatibilityCaptureWriter&) = delete;
    CompatibilityCaptureWriter& operator=(const CompatibilityCaptureWriter&) = delete;

    bool writeRecord(const std::string& jsonLine);

    // Copies the payload before returning. Availability means accepted for writing.
    // Check hasError() and the terminal capture_status record after finish().
    BlobRef storeBlob(const void* pData, size_t bytes);

    bool isTruncated() const;
    bool hasError() const;
    std::string errorMessage() const;

    // Drains accepted records and writes a terminal status. Repeated calls are safe.
    void finish();

    static std::string jsonEscape(const std::string& value);
    static std::string hashBytes(const void* pData, size_t bytes);

  private:
    class Impl;
    std::unique_ptr<Impl> m_pImpl;
  };

}
// NV-DXVK end
