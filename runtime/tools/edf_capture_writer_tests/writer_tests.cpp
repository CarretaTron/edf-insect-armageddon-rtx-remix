/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 *
 * SPDX-License-Identifier: MIT
 */

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "../../src/util/util_compat_capture.h"

namespace {
  void check(bool condition, const char* pMessage) {
    if (!condition) {
      throw std::runtime_error(pMessage);
    }
  }

  std::string readFile(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    check(bool(stream), "Cannot read test output");
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
  }

  uint64_t statusInteger(const std::string& records, const char* pName) {
    const auto status = records.rfind("{\"type\":\"capture_status\"");
    check(status != std::string::npos, "Missing terminal capture status");
    const auto key = "\"" + std::string(pName) + "\":";
    const auto position = records.find(key, status);
    check(position != std::string::npos, "Missing numeric status field");
    return std::stoull(records.substr(position + key.size()));
  }

  void testHashAndEscaping() {
    check(dxvk::CompatibilityCaptureWriter::hashBytes("abc", 3)
      == "a9993e364706816aba3e25717850c26c9cd0d89d", "SHA1 known vector mismatch");
    check(dxvk::CompatibilityCaptureWriter::hashBytes(nullptr, 0)
      == "da39a3ee5e6b4b0d3255bfef95601890afd80709", "SHA1 empty vector mismatch");
    check(dxvk::CompatibilityCaptureWriter::jsonEscape(std::string("\"\\\n\r\t\b\f\0\1", 9))
      == "\\\"\\\\\\n\\r\\t\\b\\f\\u0000\\u0001", "JSON escaping mismatch");
  }

  void testCopiesAndDeduplicates(const std::filesystem::path& root) {
    const auto output = root / "dedup";
    dxvk::CompatibilityCaptureWriter writer(output);
    check(!writer.hasError(), "Writer initialization failed");
    std::string payload = "original buffer contents";
    const auto blob = writer.storeBlob(payload.data(), payload.size());
    check(blob.available, "Blob was not accepted");
    const auto duplicate = writer.storeBlob(payload.data(), payload.size());
    check(duplicate.available && duplicate.hash == blob.hash, "Blob deduplication failed");
    payload.assign(payload.size(), 'x');
    check(writer.writeRecord("{\"type\":\"draw\",\"blob\":\"" + blob.path + "\"}"),
      "Record was not accepted");
    const auto emptyBlob = writer.storeBlob(nullptr, 0);
    check(emptyBlob.available, "Empty blob was not accepted");
    writer.finish();
    writer.finish();
    check(!writer.hasError(), "Writer failed to finish");
    check(!writer.isTruncated(), "Writer unexpectedly truncated output");
    check(!writer.writeRecord("{}"), "Writer accepted a record after finish");
    check(!writer.storeBlob("late", 4).available, "Writer accepted a blob after finish");
    check(readFile(output / blob.path) == "original buffer contents", "Payload was not copied");
    check(std::filesystem::file_size(output / emptyBlob.path) == 0, "Empty blob is not empty");
    size_t blobFiles = 0;
    for (const auto& entry : std::filesystem::directory_iterator(output / "blobs")) {
      if (entry.is_regular_file()) {
        ++blobFiles;
      }
    }
    check(blobFiles == 2, "Duplicate blob created another file");
    const auto records = readFile(output / "records.jsonl");
    check(records.find("\"truncated\":false") != std::string::npos, "Missing completion status");
    check(records.find("\"written_blobs\":2") != std::string::npos, "Wrong blob count");
    check(records.find("\"written_records\":1") != std::string::npos, "Wrong record count");

    dxvk::CompatibilityCaptureWriter replacement(output);
    check(replacement.hasError(), "Existing capture was overwritten");
    check(readFile(output / "records.jsonl") == records, "Existing capture changed");
  }

  void testByteLimit(const std::filesystem::path& root) {
    const auto output = root / "byte_limit";
    constexpr uint64_t kBudget = 2048;
    dxvk::CompatibilityCaptureWriter writer(output, kBudget);
    check(writer.writeRecord("{\"type\":\"before_limit\"}"), "Initial record was rejected");
    const std::string payload(1100, 'a');
    check(!writer.storeBlob(payload.data(), payload.size()).available, "Byte limit was not enforced");
    check(writer.isTruncated(), "Byte limit did not set truncation status");
    check(!writer.writeRecord("{}"), "Writer accepted data after truncation");
    writer.finish();
    check(!writer.hasError(), "Byte-limited writer has an I/O error");
    const auto records = readFile(output / "records.jsonl");
    check(records.find("before_limit") != std::string::npos, "Queued record was lost on truncation");
    check(records.find("\"reason\":\"byte_limit\"") != std::string::npos,
      "Byte limit reason was not recorded");
    check(records.size() <= kBudget, "Terminal status exceeded the budget");
  }

  void testQueueLimit(const std::filesystem::path& root) {
    const auto output = root / "queue_limit";
    dxvk::CompatibilityCaptureWriter writer(output);
    check(writer.writeRecord("{\"type\":\"before_limit\"}"), "Initial record was rejected");
    const std::string largeRecord(16 * 1024 * 1024, 'x');
    check(!writer.writeRecord(largeRecord), "Queue limit was not enforced");
    writer.finish();
    check(writer.isTruncated() && !writer.hasError(), "Queue limit status is incorrect");
    const auto records = readFile(output / "records.jsonl");
    check(records.find("before_limit") != std::string::npos, "Queued record was lost");
    check(records.find("\"reason\":\"queue_limit\"") != std::string::npos,
      "Queue limit reason was not recorded");

    dxvk::CompatibilityCaptureWriter blobWriter(root / "blob_queue_limit");
    const auto blob = blobWriter.storeBlob(largeRecord.data(), largeRecord.size());
    check(!blob.available && blobWriter.isTruncated(), "Oversized blob copy was accepted");
    blobWriter.finish();
    check(!blobWriter.hasError(), "Oversized blob produced an I/O error");
  }

  void testQueueConfiguration(const std::filesystem::path& root) {
    constexpr uint64_t kMiB = 1024 * 1024;
    constexpr uint64_t kQueueBytes = 64 * kMiB;
    constexpr uint64_t kDiskBytes = 256 * kMiB;
    std::string payload(20 * kMiB, 'q');
    const auto expectedHash = dxvk::CompatibilityCaptureWriter::hashBytes(payload.data(), payload.size());

    const auto defaultOutput = root / "default_queue_20mib";
    dxvk::CompatibilityCaptureWriter defaultWriter(defaultOutput);
    check(!defaultWriter.storeBlob(payload.data(), payload.size()).available,
      "Default queue accepted a 20 MiB payload");
    defaultWriter.finish();
    check(defaultWriter.isTruncated() && !defaultWriter.hasError(), "Default queue limit status is incorrect");
    const auto defaultRecords = readFile(defaultOutput / "records.jsonl");
    check(defaultRecords.find("\"reason\":\"queue_limit\"") != std::string::npos,
      "Default queue did not report queue_limit");
    check(statusInteger(defaultRecords, "max_queue_bytes") == 16 * kMiB,
      "Default queue budget changed");
    check(statusInteger(defaultRecords, "peak_queue_bytes") == 0,
      "Rejected payload was charged to the queue");

    const auto largeOutput = root / "queue_64mib";
    dxvk::CompatibilityCaptureWriter largeWriter(largeOutput, kDiskBytes, kQueueBytes);
    const auto accepted = largeWriter.storeBlob(payload.data(), payload.size());
    check(accepted.available && accepted.hash == expectedHash, "64 MiB queue rejected or corrupted the payload");
    const auto duplicate = largeWriter.storeBlob(payload.data(), payload.size());
    check(duplicate.available && duplicate.hash == accepted.hash, "Large-payload deduplication failed");
    check(largeWriter.writeRecord("{\"type\":\"after_large_blob\"}"), "64 MiB queue rejected metadata");
    payload.assign(payload.size(), 'x');
    largeWriter.finish();
    check(!largeWriter.isTruncated() && !largeWriter.hasError(), "64 MiB queue failed to drain");
    const auto copiedPayload = readFile(largeOutput / accepted.path);
    check(copiedPayload.size() == 20 * kMiB, "Large-payload file size mismatch");
    check(dxvk::CompatibilityCaptureWriter::hashBytes(copiedPayload.data(), copiedPayload.size()) == expectedHash,
      "Large-payload bytes were not preserved");
    const auto largeRecords = readFile(largeOutput / "records.jsonl");
    check(statusInteger(largeRecords, "max_queue_bytes") == kQueueBytes, "Configured queue budget was not reported");
    const auto peakBytes = statusInteger(largeRecords, "peak_queue_bytes");
    check(peakBytes > 20 * kMiB && peakBytes <= kQueueBytes, "Peak queue accounting excludes payload or exceeds budget");
    check(statusInteger(largeRecords, "written_blobs") == 1, "Large duplicate created a second blob");
    check(statusInteger(largeRecords, "written_records") == 1, "Metadata was lost while draining the queue");

    const auto diskOutput = root / "large_queue_small_disk";
    dxvk::CompatibilityCaptureWriter diskWriter(diskOutput, 8 * kMiB, kQueueBytes);
    check(!diskWriter.storeBlob(payload.data(), payload.size()).available, "Larger queue disabled the disk budget");
    diskWriter.finish();
    check(diskWriter.isTruncated() && !diskWriter.hasError(), "Independent disk budget status is incorrect");
    const auto diskRecords = readFile(diskOutput / "records.jsonl");
    check(diskRecords.find("\"reason\":\"byte_limit\"") != std::string::npos,
      "Independent disk budget did not report byte_limit");
    check(statusInteger(diskRecords, "peak_queue_bytes") == 0, "Disk-rejected payload was queued");
    check(std::filesystem::file_size(diskOutput / "records.jsonl") <= 8 * kMiB,
      "Terminal status exceeded the independent disk budget");

    dxvk::CompatibilityCaptureWriter tinyWriter(root / "tiny_queue", kDiskBytes, 1);
    check(tinyWriter.hasError() && !tinyWriter.errorMessage().empty(), "Invalid tiny queue was accepted");
    check(!tinyWriter.writeRecord("{}") && !tinyWriter.storeBlob(nullptr, 0).available,
      "Invalid queue accepted payloads");
    tinyWriter.finish();
    tinyWriter.finish();
    check(!std::filesystem::exists(root / "tiny_queue" / "records.jsonl"),
      "Invalid queue created a partial manifest");
  }

  void testConcurrentProducers(const std::filesystem::path& root) {
    const auto output = root / "concurrent";
    dxvk::CompatibilityCaptureWriter writer(output);
    std::atomic<bool> accepted(true);
    std::vector<std::thread> producers;
    for (uint32_t producer = 0; producer < 4; ++producer) {
      producers.emplace_back([producer, &writer, &accepted] {
        for (uint32_t item = 0; item < 250; ++item) {
          const auto record = "{\"producer\":" + std::to_string(producer)
            + ",\"item\":" + std::to_string(item) + "}";
          if (!writer.writeRecord(record)) {
            accepted.store(false);
          }
          const auto blob = writer.storeBlob("shared", 6);
          if (!blob.available) {
            accepted.store(false);
          }
        }
      });
    }
    for (auto& producer : producers) {
      producer.join();
    }
    writer.finish();
    check(accepted.load() && !writer.hasError(), "Concurrent producers lost data");
    const auto records = readFile(output / "records.jsonl");
    check(records.find("\"written_records\":1000") != std::string::npos,
      "Concurrent record count mismatch");
    check(records.find("\"written_blobs\":1") != std::string::npos,
      "Concurrent blob deduplication failed");
  }

  void testFailures(const std::filesystem::path& root) {
    std::ofstream(root / "file_instead_of_directory") << "occupied";
    dxvk::CompatibilityCaptureWriter failed(root / "file_instead_of_directory" / "output");
    check(failed.hasError() && !failed.errorMessage().empty(), "Filesystem failure was not reported");
    check(!failed.writeRecord("{}"), "Failed writer accepted output");
    failed.finish();

    dxvk::CompatibilityCaptureWriter smallBudget(root / "small_budget", 512);
    check(smallBudget.hasError(), "Invalid budget was not reported");

    dxvk::CompatibilityCaptureWriter nullPayload(root / "null_payload");
    check(!nullPayload.storeBlob(nullptr, 1).available, "Null blob payload was accepted");
    nullPayload.finish();
    check(nullPayload.hasError(), "Null payload error was not reported");
    check(readFile(root / "null_payload" / "records.jsonl").find("Blob payload is null")
      != std::string::npos, "Terminal record lacks payload error");

    dxvk::CompatibilityCaptureWriter lineBreak(root / "line_break");
    check(!lineBreak.writeRecord("{\n}"), "Unescaped newline was accepted");
    lineBreak.finish();
    check(lineBreak.hasError(), "Invalid JSONL record was not reported");

    const auto asynchronousPath = root / "asynchronous_failure";
    dxvk::CompatibilityCaptureWriter asynchronousFailure(asynchronousPath);
    const std::string payload = "unwritable blob";
    const auto hash = dxvk::CompatibilityCaptureWriter::hashBytes(payload.data(), payload.size());
    std::filesystem::create_directory(asynchronousPath / "blobs" / (hash + ".bin"));
    check(asynchronousFailure.storeBlob(payload.data(), payload.size()).available,
      "Asynchronous failure test did not accept payload");
    asynchronousFailure.finish();
    check(asynchronousFailure.hasError(), "Asynchronous I/O failure was not reported");
    check(readFile(asynchronousPath / "records.jsonl").find("Cannot write capture blob")
      != std::string::npos, "Terminal record lacks asynchronous I/O error");
  }
}

int main(int argc, char** ppArguments) {
  try {
    check(argc == 2, "Usage: writer_tests.exe <artifact-directory>");
    const auto runId = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root = std::filesystem::absolute(ppArguments[1]) / std::to_string(runId);
    std::filesystem::create_directories(root);
    testHashAndEscaping();
    testCopiesAndDeduplicates(root);
    testByteLimit(root);
    testQueueLimit(root);
    testQueueConfiguration(root);
    testConcurrentProducers(root);
    testFailures(root);
    std::cout << "All compatibility capture writer tests passed. Artifacts: " << root << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
