/*
* Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
*
* Permission is hereby granted, free of charge, to any person obtaining a
* copy of this software and associated documentation files (the "Software"),
* to deal in the Software without restriction, including without limitation
* the rights to use, copy, modify, merge, publish, distribute, sublicense,
* and/or sell copies of the Software, and to permit persons to whom the
* Software is furnished to do so, subject to the following conditions:
*
* The above copyright notice and this permission notice shall be included in
* all copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
* THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
* FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
* DEALINGS IN THE SOFTWARE.
*/
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>

#include "rtx_edf_static_probe.h"
#include "rtx_context.h"
#include "rtx_scene_manager.h"
#include "../../util/sync/sync_signal.h"
#ifdef _WIN32
#include <windows.h>
#endif

namespace dxvk {
  namespace {
    struct EdfProbeReplayControl {
      RTX_OPTION("rtx.edfStatic.probe", uint32_t, autorunStartFrame, 0,
        "Earliest automatic request frame for controlled replays after WSI initialization. Ignored when autorun is disabled.");
    };
    constexpr uint64_t kTraceId = 0x4544465354415449ull;
    constexpr uint32_t kMaxWaitFrames = 300;
    constexpr uint64_t kMaxPixels = 4194304;

    uint64_t imageIdentity(const Rc<DxvkImage>& image) {
      return reinterpret_cast<uintptr_t>(image.ptr());
    }

    std::string jsonString(const std::string& value) {
      std::ostringstream out;
      out << '"';
      for (unsigned char c : value) {
        if (c == '"' || c == '\\') {
          out << '\\' << c;
        } else if (c < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        } else {
          out << c;
        }
      }
      out << '"';
      return out.str();
    }

    void writeMatrix(std::ostream& out, const Matrix4& matrix) {
      out << '[';
      for (uint32_t column = 0; column < 4; column++) {
        for (uint32_t row = 0; row < 4; row++) {
          if (column || row) {
            out << ',';
          }
          const float value = matrix[column][row];
          if (std::isfinite(value)) {
            out << value;
          } else {
            out << "null";
          }
        }
      }
      out << ']';
    }

    Matrix4 copyShaderMatrix(const mat4& matrix) {
      Matrix4 result;
      std::memcpy(result.data, matrix.m, sizeof(result.data));
      return result;
    }

    void writeMatrix(std::ostream& out, const mat4& matrix) {
      writeMatrix(out, copyShaderMatrix(matrix));
    }

    double matrixError(const Matrix4& first, const Matrix4& second) {
      double error = 0;
      for (uint32_t column = 0; column < 4; column++) {
        for (uint32_t row = 0; row < 4; row++) {
          if (!std::isfinite(first[column][row]) || !std::isfinite(second[column][row])) {
            return std::numeric_limits<double>::infinity();
          }
          error = std::max(error, std::abs(double(first[column][row]) - second[column][row]));
        }
      }
      return error;
    }

    double matrixError(const Matrix4& first, const mat4& second) {
      return matrixError(first, copyShaderMatrix(second));
    }

    double matrixError(const mat4& first, const mat4& second) {
      return matrixError(copyShaderMatrix(first), copyShaderMatrix(second));
    }

    float halfToFloat(uint16_t bits) {
      const uint32_t sign = (bits & 0x8000u) << 16;
      const uint32_t exponent = (bits >> 10) & 31u;
      const uint32_t fraction = bits & 1023u;
      uint32_t result;
      if (exponent == 0) {
        if (!fraction) {
          result = sign;
        } else {
          return std::ldexp(float(fraction), -24) * (sign ? -1.f : 1.f);
        }
      } else if (exponent == 31) {
        result = sign | 0x7f800000u | (fraction << 13);
      } else {
        result = sign | ((exponent + 112u) << 23) | (fraction << 13);
      }
      float value;
      std::memcpy(&value, &result, sizeof(value));
      return value;
    }

    uint8_t displayChannel(float value) {
      if (!std::isfinite(value) || value <= 0.f) {
        return 0;
      }
      const float mapped = value / (1.f + value);
      return uint8_t(std::clamp(std::pow(mapped, 1.f / 2.2f) * 255.f, 0.f, 255.f));
    }

    uint64_t checksum(const uint8_t* pBytes, size_t byteCount) {
      uint64_t value = 14695981039346656037ull;
      for (size_t i = 0; i < byteCount; i++) {
        value = (value ^ pBytes[i]) * 1099511628211ull;
      }
      return value;
    }

    struct ReadbackImage {
      const char* pName = nullptr;
      VkFormat format = VK_FORMAT_UNDEFINED;
      VkExtent3D extent = {};
      uint32_t bytesPerPixel = 0;
      Rc<DxvkBuffer> buffer;
    };

    struct PreservedReadback {
      uint64_t sessionId = 0;
      uint32_t slot = 0;
      VkFormat format = VK_FORMAT_UNDEFINED;
      VkExtent3D extent = {};
      Rc<DxvkBuffer> before;
      Rc<DxvkBuffer> after;
    };

    struct ProbeReadback {
      uint32_t frameId = 0;
      uint64_t requestId = 0;
      uint32_t drawCount = 0;
      uint32_t cameraFrame = kInvalidFrameIndex;
      uint64_t sourceTargetId = 0;
      uint64_t injectionTargetId = 0;
      uint64_t injectionTargetHash = 0;
      uint32_t injectionTargetFormat = 0;
      VkExtent3D injectionTargetExtent = {};
      Matrix4 sourceView;
      Matrix4 sourceProjection;
      Camera camera;
      float jitter[2] = {};
      float missLinearZ = 0;
      uint32_t surfaceCount = 0;
      uint32_t lightCount = 0;
      uint32_t maxBounces = 0;
      uint64_t instanceId = 0;
      uint64_t geometryHash = 0;
      uint64_t materialHash = 0;
      std::filesystem::path directory;
      std::array<ReadbackImage, 4> images;
      std::vector<PreservedReadback> originals;
    };

    void writeReadback(const std::shared_ptr<ProbeReadback>& job) {
      try {
        std::filesystem::create_directories(job->directory);
        const std::string prefix = "edf_pt_request_" + std::to_string(job->requestId) + "_frame_" + std::to_string(job->frameId);
        std::ostringstream metadata;
        metadata << std::setprecision(9)
          << "{\n\"schema\":1,\"traceId\":\"0x4544465354415449\","
          << "\"gpuCompleted\":true,\"rasterPreservedByDesign\":true,\"compositionPerformed\":false,"
          << "\"frameId\":" << job->frameId << ",\"requestId\":" << job->requestId
          << ",\"currentFrameDrawCount\":" << job->drawCount << ",\"cameraLastUpdateFrame\":" << job->cameraFrame
          << ",\"surfaceCount\":" << job->surfaceCount << ",\"lightCount\":" << job->lightCount
          << ",\"pathMaxBounces\":" << job->maxBounces << ",\"matrixStorage\":\"column_major\",\n"
          << "\"whitelist\":{\"orderedInstances\":1,\"instanceId\":" << job->instanceId
          << ",\"vertices\":252,\"indices\":432,\"sourceMeshKey\":\"925aa18481228b01392a574dbe28ba4e21aa1616\","
          << "\"decoderVertexShader\":\"f098f457d9ae603bea5f84ec985d473c29cfb0c9\","
          << "\"decoderPixelShader\":\"44b92a29eb3e5f2ee6d53f02e14fb11a01511da8\","
          << "\"geometryHash\":" << jsonString(str::format("0x", std::hex, job->geometryHash))
          << ",\"materialHash\":" << jsonString(str::format("0x", std::hex, job->materialHash)) << "},\n"
          << "\"sourceTargetSessionId\":" << job->sourceTargetId
          << ",\"injectionTargetSessionId\":" << job->injectionTargetId
          << ",\"injectionTargetContentHash\":" << jsonString(str::format("0x", std::hex, job->injectionTargetHash))
          << ",\"injectionTargetFormat\":" << job->injectionTargetFormat
          << ",\"injectionTargetExtent\":[" << job->injectionTargetExtent.width << ',' << job->injectionTargetExtent.height << "],\n"
          << "\"sourceWorldToView\":";
        writeMatrix(metadata, job->sourceView);
        metadata << ",\"sourceViewToProjection\":";
        writeMatrix(metadata, job->sourceProjection);
        metadata << ",\"rendererWorldToView\":";
        writeMatrix(metadata, job->camera.worldToView);
        metadata << ",\"rendererViewToProjection\":";
        writeMatrix(metadata, job->camera.viewToProjection);
        metadata << ",\"rendererViewToWorld\":";
        writeMatrix(metadata, job->camera.viewToWorld);
        metadata << ",\"rendererProjectionToView\":";
        writeMatrix(metadata, job->camera.projectionToView);
        metadata << ",\"rendererWorldToProjectionJittered\":";
        writeMatrix(metadata, job->camera.worldToProjectionJittered);
        metadata << ",\"rendererProjectionToWorldJittered\":";
        writeMatrix(metadata, job->camera.projectionToWorldJittered);
        metadata << ",\"rendererViewToProjectionJittered\":";
        writeMatrix(metadata, job->camera.viewToProjectionJittered);
        metadata << ",\"rendererViewError\":" << matrixError(job->sourceView, job->camera.worldToView)
          << ",\"rendererProjectionError\":" << matrixError(job->sourceProjection, job->camera.viewToProjection)
          << ",\"jitterPixels\":[" << job->jitter[0] << ',' << job->jitter[1] << "]"
          << ",\"rendererResolution\":[" << job->camera.resolution.x << ',' << job->camera.resolution.y << "]"
          << ",\"rendererCameraFlags\":" << job->camera.flags << ",\"rendererNearPlane\":" << job->camera.nearPlane
          << ",\"missLinearZ\":" << job->missLinearZ << ",\n\"images\":[";

        uint64_t hitCount = 0;
        uint64_t depthNonfiniteCount = 0;
        uint64_t radianceNonzeroCount = 0;
        uint64_t radianceNonfiniteCount = 0;
        float minimumHitZ = std::numeric_limits<float>::infinity();
        float maximumHitZ = -std::numeric_limits<float>::infinity();
        uint32_t hitMinX = std::numeric_limits<uint32_t>::max();
        uint32_t hitMinY = std::numeric_limits<uint32_t>::max();
        uint32_t hitMaxX = 0;
        uint32_t hitMaxY = 0;
        for (uint32_t imageIndex = 0; imageIndex < job->images.size(); imageIndex++) {
          const auto& image = job->images[imageIndex];
          const auto* pBytes = static_cast<const uint8_t*>(image.buffer->mapPtr(0));
          if (pBytes == nullptr) {
            throw std::runtime_error("Readback buffer was not host mapped.");
          }
          const size_t pixels = size_t(image.extent.width) * image.extent.height;
          const size_t byteCount = pixels * image.bytesPerPixel;
          const std::string basename = prefix + '_' + image.pName;
          std::ofstream raw(job->directory / (basename + ".bin"), std::ios::binary);
          raw.write(reinterpret_cast<const char*>(pBytes), std::streamsize(byteCount));
          raw.close();
          if (!raw) {
            throw std::runtime_error("Could not write raw readback image.");
          }
          if (imageIndex) {
            metadata << ',';
          }
          metadata << "{\"name\":" << jsonString(image.pName)
            << ",\"file\":" << jsonString(basename + ".bin") << ",\"format\":" << uint32_t(image.format)
            << ",\"width\":" << image.extent.width << ",\"height\":" << image.extent.height
            << ",\"bytesPerPixel\":" << image.bytesPerPixel << ",\"byteCount\":" << byteCount
            << ",\"checksumAlgorithm\":\"fnv1a64\",\"checksum\":"
            << jsonString(str::format("0x", std::hex, checksum(pBytes, byteCount))) << '}';

          std::ofstream ppm;
          if (imageIndex) {
            ppm.open(job->directory / (basename + ".ppm"), std::ios::binary);
            ppm << "P6\n" << image.extent.width << ' ' << image.extent.height << "\n255\n";
          }
          for (size_t pixel = 0; pixel < pixels; pixel++) {
            const auto* pPixel = pBytes + pixel * image.bytesPerPixel;
            if (!imageIndex) {
              float z;
              std::memcpy(&z, pPixel, sizeof(z));
              if (!std::isfinite(z)) {
                depthNonfiniteCount++;
              } else if (z != job->missLinearZ) {
                hitCount++;
                minimumHitZ = std::min(minimumHitZ, z);
                maximumHitZ = std::max(maximumHitZ, z);
                const uint32_t x = uint32_t(pixel % image.extent.width);
                const uint32_t y = uint32_t(pixel / image.extent.width);
                hitMinX = std::min(hitMinX, x);
                hitMinY = std::min(hitMinY, y);
                hitMaxX = std::max(hitMaxX, x);
                hitMaxY = std::max(hitMaxY, y);
              }
            } else {
              uint8_t channels[3];
              if (image.format == VK_FORMAT_A2B10G10R10_UNORM_PACK32) {
                uint32_t packed;
                std::memcpy(&packed, pPixel, sizeof(packed));
                for (uint32_t channel = 0; channel < 3; channel++) {
                  channels[channel] = uint8_t(((packed >> (10 * channel)) & 1023u) * 255u / 1023u);
                }
              } else {
                bool nonzero = false;
                bool nonfinite = false;
                for (uint32_t channel = 0; channel < 3; channel++) {
                  uint16_t packed;
                  std::memcpy(&packed, pPixel + channel * sizeof(packed), sizeof(packed));
                  const float value = halfToFloat(packed);
                  channels[channel] = displayChannel(value);
                  nonzero = nonzero || (std::isfinite(value) && value > 0.f);
                  nonfinite = nonfinite || !std::isfinite(value);
                }
                if (imageIndex == 2) {
                  radianceNonzeroCount += nonzero;
                  radianceNonfiniteCount += nonfinite;
                }
              }
              ppm.write(reinterpret_cast<const char*>(channels), sizeof(channels));
            }
          }
          if (imageIndex) {
            ppm.close();
            if (!ppm) {
              throw std::runtime_error("Could not write readback preview.");
            }
          }
        }
        metadata << "],\n\"preservedTargets\":[";
        bool originalTargetsUnchanged = true;
        for (size_t index = 0; index < job->originals.size(); index++) {
          const auto& original = job->originals[index];
          const auto* pBefore = static_cast<const uint8_t*>(original.before->mapPtr(0));
          const auto* pAfter = static_cast<const uint8_t*>(original.after->mapPtr(0));
          const size_t byteCount = size_t(original.extent.width) * original.extent.height * 4;
          if (pBefore == nullptr || pAfter == nullptr) {
            throw std::runtime_error("Original target readback was not host mapped.");
          }
          const bool unchanged = std::memcmp(pBefore, pAfter, byteCount) == 0;
          originalTargetsUnchanged = originalTargetsUnchanged && unchanged;
          const std::string basename = prefix + "_original_target_" + std::to_string(original.slot);
          const uint8_t* copies[] = { pBefore, pAfter };
          const char* suffixes[] = { "_before.bin", "_after.bin" };
          for (uint32_t copy = 0; copy < 2; copy++) {
            std::ofstream raw(job->directory / (basename + suffixes[copy]), std::ios::binary);
            raw.write(reinterpret_cast<const char*>(copies[copy]), std::streamsize(byteCount));
            raw.close();
            if (!raw) {
              throw std::runtime_error("Could not write original target preservation evidence.");
            }
          }
          if (index) {
            metadata << ',';
          }
          metadata << "{\"slot\":" << original.slot << ",\"sessionId\":" << original.sessionId
            << ",\"format\":" << uint32_t(original.format) << ",\"width\":" << original.extent.width
            << ",\"height\":" << original.extent.height << ",\"byteCount\":" << byteCount
            << ",\"unchanged\":" << (unchanged ? "true" : "false")
            << ",\"comparison\":\"full_memcmp\",\"beforeFile\":" << jsonString(basename + suffixes[0])
            << ",\"afterFile\":" << jsonString(basename + suffixes[1])
            << ",\"beforeFnv1a64\":" << jsonString(str::format("0x", std::hex, checksum(pBefore, byteCount)))
            << ",\"afterFnv1a64\":" << jsonString(str::format("0x", std::hex, checksum(pAfter, byteCount))) << '}';
        }
        metadata << "],\"originalTargetsUnchanged\":" << (originalTargetsUnchanged ? "true" : "false")
          << ",\n\"depthHitCount\":" << hitCount
          << ",\"depthNonfiniteCount\":" << depthNonfiniteCount
          << ",\"radianceNonzeroCount\":" << radianceNonzeroCount
          << ",\"radianceNonfiniteCount\":" << radianceNonfiniteCount
          << ",\"radianceStage\":\"existing_renderer_after_postprocessing\","
          << "\"previewTransfer\":\"albedo_linear_RGB8; HDR_Reinhard_gamma2.2\",\"hitBounds\":";
        if (hitCount) {
          metadata << '[' << hitMinX << ',' << hitMinY << ',' << hitMaxX << ',' << hitMaxY << ']'
            << ",\"minimumHitLinearZ\":" << minimumHitZ << ",\"maximumHitLinearZ\":" << maximumHitZ;
        } else {
          metadata << "null,\"minimumHitLinearZ\":null,\"maximumHitLinearZ\":null";
        }
        metadata << "\n}\n";
        std::ofstream json(job->directory / (prefix + ".json"));
        json << metadata.str();
        json.close();
        if (!json) {
          throw std::runtime_error("Could not write readback metadata.");
        }
        Logger::info(str::format("[EDF-Static-Probe] stage=gpu_completed frame=", job->frameId,
          " request_id=", job->requestId, " depth_hits=", hitCount, " depth_nonfinite=", depthNonfiniteCount,
          " radiance_nonzero=", radianceNonzeroCount, " radiance_nonfinite=", radianceNonfiniteCount,
          " original_targets_unchanged=", unsigned(originalTargetsUnchanged), " preserved_target_count=", job->originals.size(),
          " composition_performed=0 raster_preserved=", unsigned(originalTargetsUnchanged),
          " output=", (job->directory / (prefix + ".json")).string()));
      } catch (const std::exception& error) {
        Logger::err(str::format("[EDF-Static-Probe] stage=readback_failed frame=", job->frameId,
          " request_id=", job->requestId, " gpu_completed=1 reason=", error.what()));
      }
    }
  }

  void EdfStaticProbe::noteDraw(uint32_t frameId, const Matrix4& worldToView,
    const Matrix4& viewToProjection, const std::array<Rc<DxvkImage>, 3>& sourceTargets) {
    if (m_latestDrawFrame != frameId) {
      m_drawCount = 0;
    }
    m_latestDrawFrame = frameId;
    m_drawCount++;
    m_sourceView = worldToView;
    m_sourceProjection = viewToProjection;
    m_sourceTargets = sourceTargets;
  }

  uint32_t EdfStaticProbe::currentDrawCount(uint32_t frameId) const {
    return m_latestDrawFrame == frameId ? m_drawCount : 0;
  }

  EdfStaticProbe::CompositionSource EdfStaticProbe::getCompositionSource(uint32_t frameId) const {
    CompositionSource source;
    if (m_readbackFrame != frameId || currentDrawCount(frameId) != 1 || !m_remainingFrames) {
      return source;
    }
    source.frameId = frameId;
    source.cameraFrame = m_latestDrawFrame;
    source.requestId = m_requestSerial;
    source.instanceId = m_probeInstanceId;
    source.geometryHash = m_probeGeometryHash;
    source.directory = m_outputSessionDirectory;
    for (const auto& original : m_preservedTargets) {
      source.originals.push_back({ original.image, original.before, original.slot });
    }
    return source;
  }

  void EdfStaticProbe::cancel(const char* pReason) {
    if (m_remainingFrames) {
      Logger::info(str::format("[EDF-Static-Probe] stage=cancelled request_id=", m_requestSerial,
        " remaining_frames=", m_remainingFrames, " reason=", pReason));
    }
    m_remainingFrames = 0;
    m_readbackFrame = kInvalidFrameIndex;
    m_injectionTarget = nullptr;
    m_preservedTargets.clear();
  }

  void EdfStaticProbe::pollControls(uint32_t frameId) {
    if (m_lastControlFrame == frameId) {
      return;
    }
    m_lastControlFrame = frameId;
    if (!enabled()) {
      cancel("option_disabled");
      return;
    }
#ifdef _WIN32
    const bool cancelDown = cancelKey() && (::GetAsyncKeyState(int(cancelKey())) & 0x8000);
    const bool armDown = armKey() && (::GetAsyncKeyState(int(armKey())) & 0x8000);
#else
    const bool cancelDown = false;
    const bool armDown = false;
#endif
    if (cancelDown && !m_cancelKeyDown) {
      cancel("hotkey");
      m_autorunUsed = m_autorunUsed || autorun();
    }
    const bool automatic = autorun() && !m_autorunUsed && frameId >= EdfProbeReplayControl::autorunStartFrame();
    if (!cancelDown && (automatic || (armDown && !m_armKeyDown))) {
      m_autorunUsed = m_autorunUsed || automatic;
      if (m_remainingFrames || m_readbacksInFlight->load() > 0) {
        Logger::info(str::format("[EDF-Static-Probe] stage=arm_rejected frame=", frameId,
          " reason=previous_request_pending readbacks_in_flight=", m_readbacksInFlight->load()));
      } else {
        if (m_outputSessionDirectory.empty()) {
          const auto timestamp = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
#ifdef _WIN32
          const uint32_t processId = ::GetCurrentProcessId();
#else
          const uint32_t processId = 0;
#endif
          m_outputSessionDirectory = (std::filesystem::path(outputDirectory())
            / ("session_" + std::to_string(timestamp) + '_' + std::to_string(processId))).string();
        }
        m_remainingFrames = std::clamp(frameCount(), 1u, 3u);
        m_armFrame = frameId;
        m_requestSerial++;
        Logger::info(str::format("[EDF-Static-Probe] stage=armed frame=", frameId,
          " request_id=", m_requestSerial, " frame_count=", m_remainingFrames,
          " trigger=", automatic ? "autorun" : "hotkey", " composition_performed=0 output_directory=", m_outputSessionDirectory));
      }
    }
    m_armKeyDown = armDown;
    m_cancelKeyDown = cancelDown;
    if (m_remainingFrames && frameId > m_armFrame && frameId - m_armFrame > kMaxWaitFrames) {
      cancel("no_compatible_frame_within_300_frames");
    }
  }

  void EdfStaticProbe::recordInjection(RtxContext* pContext, uint32_t frameId, const char* pSource,
    uint32_t lastInjected, const Rc<DxvkImage>& target) {
    pollControls(frameId);
    m_injectionSerial++;
    const auto& camera = pContext->getSceneManager().getCamera();
    const uint32_t count = currentDrawCount(frameId);
    const bool valid = camera.isValid(frameId);
    const bool duplicate = lastInjected == frameId;
    const uint32_t sourceIndex = std::strcmp(pSource, "present_fallback") == 0 ? 0u : 1u;
    auto& state = m_injectionStates[sourceIndex];
    state.count++;
    uint64_t signature = uint64_t(valid) | (uint64_t(duplicate) << 1) | (uint64_t(count > 0) << 2)
      | (uint64_t(count > 1) << 3) | (uint64_t(target != nullptr) << 4)
      | (uint64_t(target != nullptr && target == m_sourceTargets[0]) << 5);
    if (target != nullptr) {
      signature ^= uint64_t(target->info().format) << 8;
      signature ^= uint64_t(target->info().extent.width) << 20;
      signature ^= uint64_t(target->info().extent.height) << 40;
    }
    if (state.count <= 8 || state.signature != signature || m_remainingFrames
      || (frameId % 120 == 0 && state.lastLoggedFrame != frameId)) {
      const auto shaderCamera = camera.getShaderConstants();
      Logger::info(str::format("[EDF-Static-Probe] stage=injection_snapshot frame=", frameId,
        " serial=", m_injectionSerial, " source=", pSource, " last_injected_frame=", lastInjected,
        " latest_draw_frame=", m_latestDrawFrame, " current_frame_draw_count=", count,
        " camera_last_update_frame=", camera.getLastUpdateFrame(), " camera_valid=", unsigned(valid),
        " duplicate=", unsigned(duplicate), " target_session_id=", imageIdentity(target),
        " source_target_session_id=", imageIdentity(m_sourceTargets[0]),
        " target_hash=", target != nullptr ? target->getHash() : 0,
        " target_format=", target != nullptr ? uint32_t(target->info().format) : 0,
        " target_width=", target != nullptr ? target->info().extent.width : 0,
        " target_height=", target != nullptr ? target->info().extent.height : 0,
        " renderer_view_error=", count ? matrixError(m_sourceView, shaderCamera.worldToView) : -1,
        " renderer_projection_error=", count ? matrixError(m_sourceProjection, shaderCamera.viewToProjection) : -1));
      state.signature = signature;
      state.lastLoggedFrame = frameId;
    }
  }

  bool EdfStaticProbe::beginFrame(RtxContext* pContext, const Rc<DxvkImage>& injectionTarget) {
    const uint32_t frameId = pContext->getDevice()->getCurrentFrameId();
    pollControls(frameId);
    if (!enabled() || !m_remainingFrames || m_lastProbeFrame == frameId) {
      return false;
    }
    if (currentDrawCount(frameId) != 1 || injectionTarget == nullptr) {
      Logger::info(str::format("[EDF-Static-Probe] stage=probe_rejected frame=", frameId,
        " reason=requires_exactly_one_current_frame_draw_and_target current_frame_draw_count=", currentDrawCount(frameId)));
      return false;
    }
    const auto& camera = pContext->getSceneManager().getCamera();
    const auto& instances = pContext->getSceneManager().getAccelManager().getOrderedInstances();
    if (instances.size() != 1 || instances[0]->getBlas() == nullptr
      || instances[0]->getBlas()->input.compatibilityTraceId != kTraceId
      || instances[0]->isHidden() || instances[0]->isMarkedForGC() || !instances[0]->getVkInstance().mask) {
      Logger::info(str::format("[EDF-Static-Probe] stage=probe_rejected frame=", frameId,
        " reason=requires_one_visible_validated_instance ordered_instances=", instances.size()));
      return false;
    }
    const auto& inputGeometry = instances[0]->getBlas()->input.getGeometryData();
    const auto& geometry = instances[0]->getBlas()->modifiedGeometryData;
    const uint64_t geometryHash = geometry.hashes.getHashForRule<rules::FullGeometryHash>();
    if (inputGeometry.vertexCount != 252 || inputGeometry.indexCount != 432
      || geometry.vertexCount != 252 || geometry.indexCount != 432
      || geometryHash != inputGeometry.getHashForRule<rules::FullGeometryHash>()) {
      Logger::info(str::format("[EDF-Static-Probe] stage=probe_rejected frame=", frameId,
        " reason=validated_geometry_changed vertices=", geometry.vertexCount, " indices=", geometry.indexCount));
      return false;
    }
    m_probeInstanceId = instances[0]->getId();
    m_probeGeometryHash = geometryHash;
    m_probeMaterialHash = instances[0]->getMaterialHash();
    const auto shaderCamera = camera.getShaderConstants();
    const double viewError = matrixError(m_sourceView, shaderCamera.worldToView);
    const double projectionError = matrixError(m_sourceProjection, shaderCamera.viewToProjection);
    const double jitterError = matrixError(shaderCamera.viewToProjection, shaderCamera.viewToProjectionJittered);
    const auto extent = injectionTarget->info().extent;
    if (!camera.isValid(frameId) || viewError > 0.0001 || projectionError > 0.0001 || jitterError > 0.000001
      || extent.depth != 1 || !extent.width || !extent.height || uint64_t(extent.width) * extent.height > kMaxPixels) {
      Logger::info(str::format("[EDF-Static-Probe] stage=probe_rejected frame=", frameId,
        " reason=camera_or_extent_guard camera_valid=", unsigned(camera.isValid(frameId)),
        " renderer_view_error=", viewError, " renderer_projection_error=", projectionError,
        " renderer_jitter_matrix_error=", jitterError, " width=", extent.width, " height=", extent.height));
      return false;
    }
    const std::array<Rc<DxvkImage>, 4> originals = {
      injectionTarget, m_sourceTargets[0], m_sourceTargets[1], m_sourceTargets[2]
    };
    for (const auto& original : originals) {
      if (original == nullptr || original->info().sampleCount != VK_SAMPLE_COUNT_1_BIT
        || original->info().numLayers != 1 || original->info().extent != extent
        || (original->info().format != VK_FORMAT_B8G8R8A8_UNORM
          && original->info().format != VK_FORMAT_B8G8R8A8_SRGB
          && original->info().format != VK_FORMAT_R8G8B8A8_UNORM
          && original->info().format != VK_FORMAT_R8G8B8A8_SRGB)) {
        Logger::info(str::format("[EDF-Static-Probe] stage=probe_rejected frame=", frameId,
          " reason=original_target_format_extent_or_multisample_guard"));
        return false;
      }
    }
    m_preservedTargets.clear();
    for (uint32_t slot = 0; slot < originals.size(); slot++) {
      const auto& image = originals[slot];
      const bool duplicate = std::any_of(m_preservedTargets.begin(), m_preservedTargets.end(),
        [&](const PreservedTarget& entry) { return entry.image == image; });
      if (duplicate) {
        continue;
      }
      DxvkBufferCreateInfo info = {};
      info.size = VkDeviceSize(extent.width) * extent.height * 4;
      info.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
      info.stages = VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_HOST_BIT;
      info.access = VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_HOST_READ_BIT;
      auto buffer = pContext->getDevice()->createBuffer(info,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
        DxvkMemoryStats::Category::RTXBuffer, "EDF original target before probe");
      const VkImageSubresourceLayers subresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
      pContext->copyImageToBuffer(buffer, 0, 0, 0, image, subresource, { 0, 0, 0 }, extent);
      m_preservedTargets.push_back({ image, buffer, slot });
    }
    m_lastProbeFrame = frameId;
    m_readbackFrame = frameId;
    m_injectionTarget = injectionTarget;
    Logger::info(str::format("[EDF-Static-Probe] stage=whitelist_accepted frame=", frameId,
      " instance_id=", m_probeInstanceId, " ordered_instances=1 vertices=252 indices=432 geometry_hash=0x",
      std::hex, m_probeGeometryHash, std::dec, " mesh=925aa18481228b01392a574dbe28ba4e21aa1616",
      " vs=f098f457d9ae603bea5f84ec985d473c29cfb0c9 ps=44b92a29eb3e5f2ee6d53f02e14fb11a01511da8"));
    Logger::info(str::format("[EDF-Static-Probe] stage=offscreen_begin frame=", frameId,
      " request_id=", m_requestSerial, " trace=0x", std::hex, kTraceId, std::dec,
      " camera_last_update_frame=", camera.getLastUpdateFrame(), " renderer_view_error=", viewError,
      " renderer_projection_error=", projectionError, " composition_performed=0 raster_preserved=1"));
    return true;
  }

  void EdfStaticProbe::queueReadback(RtxContext* pContext, const Resources::RaytracingOutput& output) {
    const uint32_t frameId = pContext->getDevice()->getCurrentFrameId();
    if (m_readbackFrame != frameId || !m_remainingFrames) {
      return;
    }
    auto job = std::make_shared<ProbeReadback>();
    job->frameId = frameId;
    job->requestId = m_requestSerial;
    job->drawCount = currentDrawCount(frameId);
    job->cameraFrame = pContext->getSceneManager().getCamera().getLastUpdateFrame();
    job->sourceTargetId = imageIdentity(m_sourceTargets[0]);
    job->injectionTargetId = imageIdentity(m_injectionTarget);
    job->injectionTargetHash = m_injectionTarget->getHash();
    job->injectionTargetFormat = uint32_t(m_injectionTarget->info().format);
    job->injectionTargetExtent = m_injectionTarget->info().extent;
    job->sourceView = m_sourceView;
    job->sourceProjection = m_sourceProjection;
    job->camera = output.m_raytraceArgs.camera;
    pContext->getSceneManager().getCamera().getJittering(job->jitter);
    job->missLinearZ = output.m_raytraceArgs.primaryDirectMissLinearViewZ;
    job->surfaceCount = output.m_raytraceArgs.surfaceCount;
    job->lightCount = output.m_raytraceArgs.lightCount;
    job->maxBounces = output.m_raytraceArgs.pathMaxBounces;
    job->instanceId = m_probeInstanceId;
    job->geometryHash = m_probeGeometryHash;
    job->materialHash = m_probeMaterialHash;
    job->directory = std::filesystem::path(m_outputSessionDirectory);
    const std::array<Rc<DxvkImage>, 4> sources = {
      output.m_primaryLinearViewZ.image,
      output.m_primaryAlbedo.image,
      output.m_primaryDirectDiffuseRadiance.image(Resources::AccessType::Read),
      output.m_finalOutput.image(Resources::AccessType::Read)
    };
    constexpr const char* kNames[] = { "linear_z", "albedo", "direct_diffuse_radiance", "final_offscreen" };
    constexpr VkFormat kFormats[] = { VK_FORMAT_R32_SFLOAT, VK_FORMAT_A2B10G10R10_UNORM_PACK32,
      VK_FORMAT_R16G16B16A16_SFLOAT, VK_FORMAT_R16G16B16A16_SFLOAT };
    constexpr uint32_t kBytes[] = { 4, 4, 8, 8 };
    for (uint32_t index = 0; index < sources.size(); index++) {
      if (sources[index] == nullptr || sources[index]->info().format != kFormats[index]
        || sources[index]->info().extent != sources[0]->info().extent) {
        cancel("unsupported_readback_image_format_or_extent");
        return;
      }
    }
    for (uint32_t index = 0; index < sources.size(); index++) {
      auto& image = job->images[index];
      image.pName = kNames[index];
      image.format = kFormats[index];
      image.extent = sources[index]->info().extent;
      image.bytesPerPixel = kBytes[index];
      DxvkBufferCreateInfo info = {};
      info.size = VkDeviceSize(image.extent.width) * image.extent.height * image.bytesPerPixel;
      info.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
      info.stages = VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_HOST_BIT;
      info.access = VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_HOST_READ_BIT;
      image.buffer = pContext->getDevice()->createBuffer(info,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
        DxvkMemoryStats::Category::RTXBuffer, "EDF offscreen probe readback");
      const VkImageSubresourceLayers subresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
      pContext->copyImageToBuffer(image.buffer, 0, 0, 0, sources[index], subresource, { 0, 0, 0 }, image.extent);
    }
    for (const auto& original : m_preservedTargets) {
      PreservedReadback target;
      target.sessionId = imageIdentity(original.image);
      target.slot = original.slot;
      target.format = original.image->info().format;
      target.extent = original.image->info().extent;
      target.before = original.before;
      DxvkBufferCreateInfo info = original.before->info();
      target.after = pContext->getDevice()->createBuffer(info,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
        DxvkMemoryStats::Category::RTXBuffer, "EDF original target after probe");
      const VkImageSubresourceLayers subresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
      pContext->copyImageToBuffer(target.after, 0, 0, 0, original.image, subresource, { 0, 0, 0 }, target.extent);
      job->originals.push_back(std::move(target));
    }
    pContext->emitMemoryBarrier(0, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
      VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_HOST_READ_BIT);
    Rc<sync::CallbackFence> fence = new sync::CallbackFence{};
    m_readbacksInFlight->fetch_add(1);
    fence->setCallback(1, [job, counter = m_readbacksInFlight] {
      writeReadback(job);
      counter->fetch_sub(1);
    });
    pContext->signal(fence, 1);
    Logger::info(str::format("[EDF-Static-Probe] stage=readback_recorded frame=", frameId,
      " request_id=", m_requestSerial, " image_count=4 composition_performed=0 raster_preserved=1"));
    m_remainingFrames--;
    m_readbackFrame = kInvalidFrameIndex;
    m_injectionTarget = nullptr;
    m_preservedTargets.clear();
  }
}
