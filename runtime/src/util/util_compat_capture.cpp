/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 *
 * SPDX-License-Identifier: MIT
 */

#include <algorithm>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <fstream>
#include <limits>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include "sha1/sha1_util.h"
#include "util_compat_capture.h"

// NV-DXVK start: Opt-in compatibility diagnostics.
namespace dxvk {

  namespace {
    constexpr uint64_t kTerminalReserve = 1024;

    struct CaptureItem {
      std::string record;
      std::string hash;
      std::vector<uint8_t> payload;

      size_t memoryBytes() const {
        return sizeof(CaptureItem) + record.size() + hash.size() + payload.size();
      }
    };
  }

  class CompatibilityCaptureWriter::Impl {
  public:
    Impl(std::filesystem::path outputDirectory, uint64_t maxBytes, uint64_t maxQueueBytes)
      : m_outputDirectory(std::move(outputDirectory)), m_maxBytes(maxBytes),
        m_maxQueueBytes(maxQueueBytes) {
      try {
        if (maxBytes < kTerminalReserve) {
          throw std::runtime_error("Capture budget must be at least 1024 bytes");
        }
        if (maxQueueBytes < sizeof(CaptureItem) + 40 || maxQueueBytes > std::numeric_limits<size_t>::max()) {
          throw std::runtime_error("Capture queue budget cannot hold a blob item or exceeds size_t");
        }
        std::filesystem::create_directories(m_outputDirectory / "blobs");
        const auto manifestPath = m_outputDirectory / "records.jsonl";
        if (std::filesystem::exists(manifestPath)) {
          throw std::runtime_error("Capture directory already contains records.jsonl");
        }
        m_records.open(manifestPath, std::ios::binary | std::ios::out);
        if (!m_records) {
          throw std::runtime_error("Cannot create records.jsonl");
        }
        m_worker = std::thread([this] { writeItems(); });
      } catch (const std::exception& error) {
        setError(error.what());
      }
    }

    ~Impl() {
      finish();
    }

    bool writeRecord(const std::string& jsonLine) {
      try {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!m_accepting) {
          return false;
        }
        if (jsonLine.empty() || jsonLine.find_first_of("\r\n") != std::string::npos) {
          setErrorLocked("JSONL record is empty or contains an unescaped line break");
          return false;
        }
        if (jsonLine.size() == std::numeric_limits<size_t>::max()) {
          truncateLocked("byte_limit");
          return false;
        }
        if (jsonLine.size() > std::numeric_limits<size_t>::max() - sizeof(CaptureItem)) {
          truncateLocked("queue_limit");
          return false;
        }
        const uint64_t diskBytes = uint64_t(jsonLine.size()) + 1;
        const size_t memoryBytes = sizeof(CaptureItem) + jsonLine.size();
        if (!reserveItem(diskBytes, memoryBytes)) {
          return false;
        }
        CaptureItem item;
        item.record = jsonLine;
        m_items.push_back(std::move(item));
        m_reservedBytes += diskBytes;
        m_queueBytes += memoryBytes;
        m_peakQueueBytes = std::max(m_peakQueueBytes, m_queueBytes);
        m_wake.notify_one();
        return true;
      } catch (const std::exception& error) {
        setError(error.what());
        return false;
      }
    }

    BlobRef storeBlob(const void* pData, size_t bytes) {
      BlobRef result;
      result.size = bytes;
      try {
        // Refuse oversized copies before hashing or allocating their payloads.
        {
          std::lock_guard<std::mutex> lock(m_mutex);
          if (!m_accepting) {
            return result;
          }
          if (bytes > m_maxQueueBytes - sizeof(CaptureItem) - 40) {
            truncateLocked("queue_limit");
            return result;
          }
        }
        if (pData == nullptr && bytes) {
          setError("Blob payload is null");
          return result;
        }
        result.hash = CompatibilityCaptureWriter::hashBytes(pData, bytes);
        result.path = "blobs/" + result.hash + ".bin";

        std::lock_guard<std::mutex> lock(m_mutex);
        if (!m_accepting) {
          return result;
        }
        const auto existing = m_blobs.find(result.hash);
        if (existing != m_blobs.end()) {
          if (existing->second != bytes) {
            setErrorLocked("SHA1 blob identity has conflicting sizes");
            return result;
          }
          result.available = true;
          return result;
        }
        const size_t memoryBytes = sizeof(CaptureItem) + result.hash.size() + bytes;
        if (!reserveItem(bytes, memoryBytes)) {
          return result;
        }
        CaptureItem item;
        item.hash = result.hash;
        item.payload.resize(bytes);
        if (bytes) {
          std::memcpy(item.payload.data(), pData, bytes);
        }
        m_items.push_back(std::move(item));
        try {
          m_blobs.emplace(result.hash, bytes);
        } catch (...) {
          m_items.pop_back();
          throw;
        }
        m_reservedBytes += bytes;
        m_queueBytes += memoryBytes;
        m_peakQueueBytes = std::max(m_peakQueueBytes, m_queueBytes);
        result.available = true;
        m_wake.notify_one();
      } catch (const std::exception& error) {
        setError(error.what());
      }
      return result;
    }

    bool isTruncated() const {
      std::lock_guard<std::mutex> lock(m_mutex);
      return m_truncated;
    }

    bool hasError() const {
      std::lock_guard<std::mutex> lock(m_mutex);
      return !m_error.empty();
    }

    std::string errorMessage() const {
      std::lock_guard<std::mutex> lock(m_mutex);
      return m_error;
    }

    void finish() {
      std::lock_guard<std::mutex> finishLock(m_finishMutex);
      {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_accepting = false;
        m_finishing = true;
      }
      m_wake.notify_one();
      if (m_worker.joinable()) {
        m_worker.join();
      }
    }

  private:
    bool reserveItem(uint64_t diskBytes, size_t memoryBytes) {
      if (diskBytes > m_maxBytes - kTerminalReserve - m_reservedBytes) {
        truncateLocked("byte_limit");
        return false;
      }
      if (memoryBytes > m_maxQueueBytes - m_queueBytes) {
        truncateLocked("queue_limit");
        return false;
      }
      return true;
    }

    void truncateLocked(const char* pReason) {
      m_truncated = true;
      m_accepting = false;
      m_truncationReason = pReason;
    }

    void setErrorLocked(const std::string& message) {
      if (m_error.empty()) {
        m_error = message.substr(0, 96);
      }
      m_accepting = false;
    }

    void setError(const std::string& message) {
      std::lock_guard<std::mutex> lock(m_mutex);
      setErrorLocked(message);
    }

    void writeItems() {
      for (;;) {
        CaptureItem item;
        {
          std::unique_lock<std::mutex> lock(m_mutex);
          m_wake.wait(lock, [this] { return m_finishing || !m_items.empty(); });
          if (m_items.empty()) {
            break;
          }
          item = std::move(m_items.front());
          m_items.pop_front();
        }
        try {
          if (item.hash.empty()) {
            m_records.write(item.record.data(), std::streamsize(item.record.size()));
            m_records.put('\n');
            if (!m_records) {
              throw std::runtime_error("Cannot write records.jsonl");
            }
            ++m_writtenRecords;
          } else {
            const auto blobPath = m_outputDirectory / "blobs" / (item.hash + ".bin");
            std::ofstream blob(blobPath, std::ios::binary | std::ios::out);
            if (!item.payload.empty()) {
              blob.write(reinterpret_cast<const char*>(item.payload.data()),
                std::streamsize(item.payload.size()));
            }
            blob.close();
            if (!blob) {
              throw std::runtime_error("Cannot write capture blob " + item.hash);
            }
            ++m_writtenBlobs;
          }
        } catch (const std::exception& error) {
          setError(error.what());
        }
        const size_t memoryBytes = item.memoryBytes();
        item = CaptureItem();
        {
          std::lock_guard<std::mutex> lock(m_mutex);
          // The in-flight payload remains charged until its storage is released.
          m_queueBytes -= memoryBytes;
        }
      }

      try {
        std::string error;
        std::string reason;
        bool truncated;
        uint64_t reservedBytes;
        size_t peakQueueBytes;
        {
          std::lock_guard<std::mutex> lock(m_mutex);
          error = m_error;
          reason = m_truncationReason;
          truncated = m_truncated;
          reservedBytes = m_reservedBytes;
          peakQueueBytes = m_peakQueueBytes;
        }
        std::ostringstream status;
        status << "{\"type\":\"capture_status\",\"truncated\":" << (truncated ? "true" : "false")
          << ",\"reason\":\"" << reason << "\",\"error\":"
          << (error.empty() ? "null" : "\"" + CompatibilityCaptureWriter::jsonEscape(error) + "\"")
          << ",\"accepted_bytes\":" << reservedBytes
          << ",\"max_bytes\":" << m_maxBytes
          << ",\"max_queue_bytes\":" << m_maxQueueBytes
          << ",\"peak_queue_bytes\":" << peakQueueBytes
          << ",\"written_records\":" << m_writtenRecords
          << ",\"written_blobs\":" << m_writtenBlobs << "}\n";
        const std::string record = status.str();
        m_records.write(record.data(), std::streamsize(record.size()));
        m_records.flush();
        if (!m_records) {
          setError("Cannot flush terminal capture status");
        }
        m_records.close();
        if (!m_records) {
          setError("Cannot close records.jsonl");
        }
      } catch (const std::exception& error) {
        setError(error.what());
      }
    }

    std::filesystem::path m_outputDirectory;
    uint64_t m_maxBytes;
    uint64_t m_maxQueueBytes;
    std::ofstream m_records;
    std::thread m_worker;
    mutable std::mutex m_mutex;
    std::mutex m_finishMutex;
    std::condition_variable m_wake;
    std::deque<CaptureItem> m_items;
    std::unordered_map<std::string, size_t> m_blobs;
    uint64_t m_reservedBytes = 0;
    size_t m_queueBytes = 0;
    size_t m_peakQueueBytes = 0;
    uint64_t m_writtenRecords = 0;
    uint64_t m_writtenBlobs = 0;
    bool m_accepting = true;
    bool m_finishing = false;
    bool m_truncated = false;
    std::string m_truncationReason;
    std::string m_error;
  };

  CompatibilityCaptureWriter::CompatibilityCaptureWriter(
    std::filesystem::path outputDirectory, uint64_t maxBytes, uint64_t maxQueueBytes)
    : m_pImpl(std::make_unique<Impl>(std::move(outputDirectory), maxBytes, maxQueueBytes)) {
  }

  CompatibilityCaptureWriter::~CompatibilityCaptureWriter() = default;

  bool CompatibilityCaptureWriter::writeRecord(const std::string& jsonLine) {
    return m_pImpl->writeRecord(jsonLine);
  }

  BlobRef CompatibilityCaptureWriter::storeBlob(const void* pData, size_t bytes) {
    return m_pImpl->storeBlob(pData, bytes);
  }

  bool CompatibilityCaptureWriter::isTruncated() const {
    return m_pImpl->isTruncated();
  }

  bool CompatibilityCaptureWriter::hasError() const {
    return m_pImpl->hasError();
  }

  std::string CompatibilityCaptureWriter::errorMessage() const {
    return m_pImpl->errorMessage();
  }

  void CompatibilityCaptureWriter::finish() {
    m_pImpl->finish();
  }

  std::string CompatibilityCaptureWriter::jsonEscape(const std::string& value) {
    constexpr char kHexDigits[] = "0123456789abcdef";
    std::string result;
    result.reserve(value.size());
    for (unsigned char character : value) {
      switch (character) {
      case '"': result += "\\\""; break;
      case '\\': result += "\\\\"; break;
      case '\b': result += "\\b"; break;
      case '\f': result += "\\f"; break;
      case '\n': result += "\\n"; break;
      case '\r': result += "\\r"; break;
      case '\t': result += "\\t"; break;
      default:
        if (character < 0x20) {
          result += "\\u00";
          result += kHexDigits[character >> 4];
          result += kHexDigits[character & 15];
        } else {
          result += char(character);
        }
        break;
      }
    }
    return result;
  }

  std::string CompatibilityCaptureWriter::hashBytes(const void* pData, size_t bytes) {
    if (pData == nullptr && bytes) {
      throw std::invalid_argument("Hash payload is null");
    }
    constexpr uint8_t kEmptyPayload = 0;
    return Sha1Hash::compute(bytes ? pData : &kEmptyPayload, bytes).toString();
  }

}
// NV-DXVK end
