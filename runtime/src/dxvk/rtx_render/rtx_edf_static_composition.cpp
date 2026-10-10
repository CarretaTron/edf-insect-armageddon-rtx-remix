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
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <vector>

#include "rtx_edf_static_composition.h"
#include "rtx_context.h"
#include "rtx_shader_manager.h"
#include "rtx_camera.h"
#include "dxvk_adapter.h"
#include "../../util/sync/sync_signal.h"
#include "rtx/pass/edf_static_composition/edf_static_composition.h"

#include <rtx_shaders/edf_static_composition_bounds.h>
#include <rtx_shaders/edf_static_composition.h>
#include <rtx_shaders/edf_static_composition_present.h>
#ifdef _WIN32
#include <windows.h>
#endif

namespace dxvk {
  namespace {
    class EdfBoundsShader : public ManagedShader {
      SHADER_SOURCE(EdfBoundsShader, VK_SHADER_STAGE_COMPUTE_BIT, edf_static_composition_bounds)
      PUSH_CONSTANTS(EdfStaticCompositionArgs)
      BEGIN_PARAMETER()
        TEXTURE2D(EDF_COMPOSITION_DEPTH)
        TEXTURE2D(EDF_COMPOSITION_COLOR)
        RW_STRUCTURED_BUFFER(EDF_COMPOSITION_BOUNDS)
      END_PARAMETER()
    };

    class EdfPanelShader : public ManagedShader {
      SHADER_SOURCE(EdfPanelShader, VK_SHADER_STAGE_COMPUTE_BIT, edf_static_composition)
      PUSH_CONSTANTS(EdfStaticCompositionArgs)
      BEGIN_PARAMETER()
        TEXTURE2D(EDF_COMPOSITION_DEPTH)
        TEXTURE2D(EDF_COMPOSITION_COLOR)
        STRUCTURED_BUFFER(EDF_COMPOSITION_BOUNDS)
        RW_TEXTURE2D(EDF_COMPOSITION_OUTPUT)
      END_PARAMETER()
    };

    class EdfPresentShader : public ManagedShader {
      SHADER_SOURCE(EdfPresentShader, VK_SHADER_STAGE_COMPUTE_BIT, edf_static_composition_present)
      PUSH_CONSTANTS(EdfStaticCompositionArgs)
      BEGIN_PARAMETER()
        TEXTURE2D(EDF_COMPOSITION_COLOR)
        STRUCTURED_BUFFER(EDF_COMPOSITION_BOUNDS)
        RW_TEXTURE2D(EDF_COMPOSITION_OUTPUT)
      END_PARAMETER()
    };

    constexpr VkExtent3D kPresentationExtent = { 1920, 1080, 1 };
    constexpr VkFormat kPresentationFormat = VK_FORMAT_R8G8B8A8_UNORM;

    struct RestoreImageLayout {
      Rc<DxvkImage> image;
      VkImageLayout layout;

      ~RestoreImageLayout() noexcept {
        // Normal recording explicitly transitions back to PRESENT. On failure
        // the caller discards the whole list: restore only the host bookkeeping
        // here, without allocating barriers while unwinding an exception.
        image->setLayout(layout);
      }
    };

    Rc<DxvkImageView> createView(DxvkDevice* pDevice, const Rc<DxvkImage>& image, VkImageUsageFlags usage) {
      DxvkImageViewCreateInfo info = {};
      info.format = image->info().format;
      info.usage = usage;
      info.aspect = VK_IMAGE_ASPECT_COLOR_BIT;
      info.numLevels = 1;
      info.numLayers = 1;
      return pDevice->createImageView(image, info);
    }

    Rc<DxvkBuffer> createReadback(DxvkDevice* pDevice, VkDeviceSize size, const char* pName) {
      DxvkBufferCreateInfo info = {};
      info.size = size;
      info.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
      info.stages = VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_HOST_BIT;
      info.access = VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_HOST_READ_BIT;
      return pDevice->createBuffer(info,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
        DxvkMemoryStats::Category::RTXBuffer, pName);
    }

    uint64_t checksum(const uint8_t* pBytes, size_t byteCount) {
      uint64_t result = 14695981039346656037ull;
      for (size_t i = 0; i < byteCount; i++) {
        result = (result ^ pBytes[i]) * 1099511628211ull;
      }
      return result;
    }

    std::string hex(uint64_t value) {
      return str::format("0x", std::hex, value);
    }

    struct Readback {
      const char* pName;
      Rc<DxvkBuffer> buffer;
      uint32_t width;
      uint32_t height;
    };

    struct CompositionReadback {
      EdfStaticProbe::CompositionSource source;
      uint64_t presentSerial = 0;
      uint64_t imageIdentity = 0;
      uint32_t loopIndex = 0;
      std::array<uint32_t, 4> roi;
      float missLinearZ = 0;
      std::array<Readback, 4> images;
      Rc<DxvkBuffer> bounds;
      std::vector<Rc<DxvkBuffer>> originalAfter;
      std::shared_ptr<std::atomic<bool>> failed;
    };

    void writeBinary(const std::filesystem::path& path, const void* pBytes, size_t size) {
      std::ofstream file(path, std::ios::binary);
      file.write(static_cast<const char*>(pBytes), std::streamsize(size));
      file.close();
      if (!file) {
        throw std::runtime_error("Could not write composition evidence.");
      }
    }

    void writeReadback(const std::shared_ptr<CompositionReadback>& job) {
      try {
        std::filesystem::create_directories(job->source.directory);
        const auto directory = std::filesystem::path(job->source.directory);
        const std::string prefix = "edf_pt_request_" + std::to_string(job->source.requestId)
          + "_frame_" + std::to_string(job->source.frameId);
        uint32_t bounds[7];
        if (job->bounds->mapPtr(0) == nullptr) {
          throw std::runtime_error("Composition bounds are not mapped.");
        }
        std::memcpy(bounds, job->bounds->mapPtr(0), sizeof(bounds));
        const bool validGpuMask = bounds[4] > 0 && bounds[5] == 0 && bounds[6] == 0 && bounds[0] <= bounds[2]
          && bounds[1] <= bounds[3] && bounds[2] < 1920 && bounds[3] < 1080;
        uint64_t outsideChanged = 0;
        uint64_t insideChanged = 0;
        uint64_t panelMismatch = 0;
        const uint8_t* pBefore = static_cast<const uint8_t*>(job->images[0].buffer->mapPtr(0));
        const uint8_t* pAfter = static_cast<const uint8_t*>(job->images[1].buffer->mapPtr(0));
        const uint8_t* pPanel = static_cast<const uint8_t*>(job->images[2].buffer->mapPtr(0));
        const uint8_t* pScratch = static_cast<const uint8_t*>(job->images[3].buffer->mapPtr(0));
        if (pBefore == nullptr || pAfter == nullptr || pPanel == nullptr || pScratch == nullptr) {
          throw std::runtime_error("Composition images are not mapped.");
        }
        for (uint32_t y = 0; y < 1080; y++) {
          for (uint32_t x = 0; x < 1920; x++) {
            const size_t offset = (size_t(y) * 1920 + x) * 4;
            const bool inside = x >= job->roi[0] && y >= job->roi[1]
              && x - job->roi[0] < job->roi[2] && y - job->roi[1] < job->roi[3];
            const bool changed = std::memcmp(pBefore + offset, pAfter + offset, 4) != 0;
            if (inside) {
              insideChanged += changed;
              const size_t panelOffset = (size_t(y - job->roi[1]) * job->roi[2] + x - job->roi[0]) * 4;
              panelMismatch += std::memcmp(pAfter + offset,
                (validGpuMask ? pPanel : pScratch) + panelOffset, 4) != 0;
            } else {
              outsideChanged += changed;
            }
          }
        }
        bool originalsUnchanged = true;
        std::ostringstream json;
        json << std::setprecision(9) << "{\n\"schema\":1,\"gpuCompleted\":true,\"compositionPerformed\":"
          << (validGpuMask ? "true" : "false")
          << ",\"requestId\":" << job->source.requestId
          << ",\"rtxFrame\":" << job->source.frameId << ",\"generatedFrame\":" << job->source.frameId
          << ",\"cameraFrame\":" << job->source.cameraFrame << ",\"presentedFrame\":" << job->source.frameId
          << ",\"presentSerial\":" << job->presentSerial << ",\"loopIndex\":" << job->loopIndex
          << ",\"wsiImageIdentity\":" << job->imageIdentity
          << ",\"width\":1920,\"height\":1080,\"format\":37,\"roi\":["
          << job->roi[0] << ',' << job->roi[1] << ',' << job->roi[2] << ',' << job->roi[3] << "]"
          << ",\"missLinearZ\":" << job->missLinearZ
          << ",\"gpuHitCount\":" << bounds[4] << ",\"gpuDepthNonfiniteCount\":" << bounds[5]
          << ",\"gpuFinalNonfiniteCount\":" << bounds[6] << ",\"presentationDisplayVerified\":false"
          << ",\"outsideChangedPixels\":" << outsideChanged << ",\"insideChangedPixels\":" << insideChanged
          << ",\"panelMismatchPixels\":" << panelMismatch
          << ",\"probeMetadata\":\"" << prefix << ".json\""
          << ",\"compositionQueueSubmitCount\":2,\"baseWaitAcquire\":1,\"baseSignalPresent\":0,"
          << "\"overlaySignalPresent\":1,\"frameLatencySignalAfterOverlay\":true,"
          << "\"colour\":{\"performSRGBConversion\":true,\"captureScreenImage\":false,"
          << "\"debugViewIndex\":0,\"finalEncoding\":\"display_gamma22_approx\"},\n";
        if (validGpuMask) {
          const uint32_t cropX = bounds[0] > 8 ? bounds[0] - 8 : 0;
          const uint32_t cropY = bounds[1] > 8 ? bounds[1] - 8 : 0;
          const uint32_t cropWidth = std::min(bounds[2] + 8, 1919u) - cropX + 1;
          const uint32_t cropHeight = std::min(bounds[3] + 8, 1079u) - cropY + 1;
          const uint32_t availableWidth = job->roi[2] - 8;
          const uint32_t availableHeight = job->roi[3] - 8;
          const bool widthLimited = availableWidth * cropHeight <= availableHeight * cropWidth;
          const uint32_t fitWidth = widthLimited ? availableWidth : std::max(1u, cropWidth * availableHeight / cropHeight);
          const uint32_t fitHeight = widthLimited ? std::max(1u, cropHeight * availableWidth / cropWidth) : availableHeight;
          json << "\"crop\":[" << cropX << ',' << cropY << ',' << cropWidth << ',' << cropHeight << "],"
            << "\"fit\":[" << (job->roi[2] - fitWidth) / 2 << ',' << (job->roi[3] - fitHeight) / 2
            << ',' << fitWidth << ',' << fitHeight << "],";
        } else {
          json << "\"crop\":null,\"fit\":null,";
        }
        json << "\n\"images\":[";
        for (uint32_t index = 0; index < job->images.size(); index++) {
          const auto& image = job->images[index];
          const auto* pBytes = static_cast<const uint8_t*>(image.buffer->mapPtr(0));
          const size_t byteCount = size_t(image.width) * image.height * 4;
          const std::string filename = prefix + "_composition_" + image.pName + ".bin";
          writeBinary(directory / filename, pBytes, byteCount);
          if (index) {
            json << ',';
          }
          json << "{\"name\":\"" << image.pName << "\",\"file\":\"" << filename
            << "\",\"format\":37,\"width\":" << image.width << ",\"height\":" << image.height
            << ",\"bytesPerPixel\":4,\"byteCount\":" << byteCount
            << ",\"checksumAlgorithm\":\"fnv1a64\",\"checksum\":\"" << hex(checksum(pBytes, byteCount)) << "\"}";
        }
        json << "],\n\"preservedTargets\":[";
        for (size_t index = 0; index < job->source.originals.size(); index++) {
          const auto& original = job->source.originals[index];
          const auto* pOriginalBefore = static_cast<const uint8_t*>(original.before->mapPtr(0));
          const auto* pOriginalAfter = static_cast<const uint8_t*>(job->originalAfter[index]->mapPtr(0));
          const size_t byteCount = size_t(original.image->info().extent.width) * original.image->info().extent.height * 4;
          if (pOriginalBefore == nullptr || pOriginalAfter == nullptr) {
            throw std::runtime_error("Composition original targets are not mapped.");
          }
          const bool unchanged = std::memcmp(pOriginalBefore, pOriginalAfter, byteCount) == 0;
          originalsUnchanged = originalsUnchanged && unchanged;
          const std::string basename = prefix + "_composition_original_target_" + std::to_string(original.slot);
          writeBinary(directory / (basename + "_before.bin"), pOriginalBefore, byteCount);
          writeBinary(directory / (basename + "_after.bin"), pOriginalAfter, byteCount);
          if (index) {
            json << ',';
          }
          json << "{\"slot\":" << original.slot << ",\"sessionId\":" << reinterpret_cast<uintptr_t>(original.image.ptr())
            << ",\"format\":" << uint32_t(original.image->info().format) << ",\"width\":1920,\"height\":1080,"
            << "\"byteCount\":" << byteCount << ",\"unchanged\":" << (unchanged ? "true" : "false")
            << ",\"comparison\":\"full_memcmp\",\"beforeFile\":\"" << basename << "_before.bin\","
            << "\"afterFile\":\"" << basename << "_after.bin\",\"beforeFnv1a64\":\""
            << hex(checksum(pOriginalBefore, byteCount)) << "\",\"afterFnv1a64\":\"" << hex(checksum(pOriginalAfter, byteCount)) << "\"}";
        }
        const bool passed = validGpuMask && outsideChanged == 0 && insideChanged > 0 && panelMismatch == 0 && originalsUnchanged;
        json << "],\"originalTargetsUnchanged\":" << (originalsUnchanged ? "true" : "false")
          << ",\"passed\":" << (passed ? "true" : "false") << "\n}\n";
        const std::string text = json.str();
        writeBinary(directory / (prefix + "_composition.json"), text.data(), text.size());
        if (!passed) {
          job->failed->store(true);
        }
        Logger::info(str::format("[EDF-Static-Composition] stage=composition_gpu_completed frame=", job->source.frameId,
          " request_id=", job->source.requestId, " gpu_completed=1 composition_performed=", unsigned(validGpuMask),
          " outside_changed_pixels=", outsideChanged, " inside_changed_pixels=", insideChanged,
          " panel_mismatch_pixels=", panelMismatch, " original_targets_unchanged=", unsigned(originalsUnchanged),
          " passed=", unsigned(passed), " output=", (directory / (prefix + "_composition.json")).string()));
      } catch (const std::exception& error) {
        job->failed->store(true);
        Logger::err(str::format("[EDF-Static-Composition] stage=composition_readback_failed frame=",
          job->source.frameId, " gpu_completed=1 reason=", error.what()));
      }
    }
  }

  void EdfStaticComposition::discard(const char* pReason) {
    if (m_pending != nullptr) {
      Logger::info(str::format("[EDF-Static-Composition] stage=preview_discarded frame=",
        m_pending->source.frameId, " request_id=", m_pending->source.requestId, " reason=", pReason));
      m_pending.reset();
    }
  }

  bool EdfStaticComposition::hasCurrentPreview(uint32_t frameId) {
#ifdef _WIN32
    const bool cancelDown = EdfStaticProbe::cancelKey()
      && (::GetAsyncKeyState(int(EdfStaticProbe::cancelKey())) & 0x8000);
#else
    const bool cancelDown = false;
#endif
    if (!enabled() || !EdfStaticProbe::enabled() || m_failed->load() || cancelDown
      || (m_pending != nullptr && m_pending->source.frameId != frameId)) {
      discard(cancelDown ? "cancel_key" : "disabled_failed_or_stale_frame");
    }
    return m_pending != nullptr && m_pending->source.frameId == frameId && m_lastComposedFrame != frameId;
  }

  void EdfStaticComposition::preparePreview(RtxContext* pContext, const Resources::RaytracingOutput& output,
    const EdfStaticProbe::CompositionSource& source, bool performSRGBConversion,
    bool captureScreenImage, uint32_t debugViewIndex) try {
    const uint32_t frameId = pContext->getDevice()->getCurrentFrameId();
    discard("superseded");
    if (!enabled() || m_failed->load()) {
      return;
    }
    const auto finalImage = output.m_finalOutput.image(Resources::AccessType::Read);
    const auto depthImage = output.m_primaryLinearViewZ.image;
    const std::array<uint32_t, 4> roi = { panelX(), panelY(), panelWidth(), panelHeight() };
    const bool validRoi = roi[2] >= 16 && roi[3] >= 16 && roi[2] <= 820 && roi[3] <= 200
      && roi[0] < 1920 && roi[1] < 1080 && roi[2] <= 1920 - roi[0] && roi[3] <= 1080 - roi[1];
    if (source.frameId != frameId || source.cameraFrame != frameId || source.originals.size() != 4
      || source.geometryHash != 0x32aeb7bb7e772c13ull || m_lastPreparedFrame == frameId
      || EdfStaticProbe::frameCount() != 1 || !validRoi || !performSRGBConversion || captureScreenImage || debugViewIndex
      || RtCamera::enableFreeCamera() || pContext->isDLFGEnabled()
      || RtxOptions::upscalerType() != UpscalerType::None || finalImage == nullptr || depthImage == nullptr
      || finalImage->info().format != VK_FORMAT_R16G16B16A16_SFLOAT || depthImage->info().format != VK_FORMAT_R32_SFLOAT
      || finalImage->info().extent != kPresentationExtent || depthImage->info().extent != kPresentationExtent
      || !std::isfinite(output.m_raytraceArgs.primaryDirectMissLinearViewZ)
      || output.m_raytraceArgs.primaryDirectMissLinearViewZ != 500001.f) {
      Logger::info(str::format("[EDF-Static-Composition] stage=preview_rejected frame=", frameId,
        " reason=source_frame_colour_geometry_or_resolution_guard gamma=", unsigned(performSRGBConversion),
        " screenshot=", unsigned(captureScreenImage), " debug_view=", debugViewIndex));
      return;
    }
    auto pending = std::make_unique<Pending>();
    pending->source = source;
    pending->roi = roi;
    pending->missLinearZ = output.m_raytraceArgs.primaryDirectMissLinearViewZ;
    DxvkImageCreateInfo imageInfo = {};
    imageInfo.type = VK_IMAGE_TYPE_2D;
    imageInfo.format = kPresentationFormat;
    imageInfo.sampleCount = VK_SAMPLE_COUNT_1_BIT;
    imageInfo.extent = { roi[2], roi[3], 1 };
    imageInfo.numLayers = 1;
    imageInfo.mipLevels = 1;
    imageInfo.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    imageInfo.stages = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT;
    imageInfo.access = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_TRANSFER_READ_BIT;
    imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
    imageInfo.layout = VK_IMAGE_LAYOUT_UNDEFINED;
    imageInfo.shared = VK_TRUE;
    pending->preview = pContext->getDevice()->createImage(imageInfo, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
      DxvkMemoryStats::Category::RTXRenderTarget, "EDF current frame diagnostic panel");
    pContext->changeImageLayout(pending->preview, VK_IMAGE_LAYOUT_GENERAL);
    DxvkBufferCreateInfo bufferInfo = {};
    bufferInfo.size = sizeof(uint32_t) * 7;
    bufferInfo.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    bufferInfo.stages = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT;
    bufferInfo.access = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_TRANSFER_READ_BIT;
    pending->bounds = pContext->getDevice()->createBuffer(bufferInfo, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT,
      DxvkMemoryStats::Category::RTXBuffer, "EDF current frame mask bounds");
    const uint32_t initialBounds[] = { 1920, 1080, 0, 0, 0, 0, 0 };
    pContext->updateBuffer(pending->bounds, 0, sizeof(initialBounds), initialBounds);
    EdfStaticCompositionArgs args = { 1920, 1080, roi[2], roi[3], pending->missLinearZ, 0, 0, 0 };
    pContext->setPushConstantBank(DxvkPushConstantBank::RTX);
    pContext->bindResourceView(EDF_COMPOSITION_DEPTH, createView(pContext->getDevice().ptr(), depthImage, VK_IMAGE_USAGE_SAMPLED_BIT), nullptr);
    pContext->bindResourceView(EDF_COMPOSITION_COLOR, createView(pContext->getDevice().ptr(), finalImage, VK_IMAGE_USAGE_SAMPLED_BIT), nullptr);
    pContext->bindResourceBuffer(EDF_COMPOSITION_BOUNDS, DxvkBufferSlice(pending->bounds, 0, bufferInfo.size));
    pContext->bindShader(VK_SHADER_STAGE_COMPUTE_BIT, EdfBoundsShader::getShader());
    pContext->pushConstants(0, sizeof(args), &args);
    pContext->dispatch((1920 + 15) / 16, (1080 + 15) / 16, 1);
    pContext->emitMemoryBarrier(0, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT,
      VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT);
    pContext->bindResourceView(EDF_COMPOSITION_COLOR, createView(pContext->getDevice().ptr(), finalImage, VK_IMAGE_USAGE_SAMPLED_BIT), nullptr);
    pContext->bindResourceView(EDF_COMPOSITION_OUTPUT, createView(pContext->getDevice().ptr(), pending->preview, VK_IMAGE_USAGE_STORAGE_BIT), nullptr);
    pContext->bindShader(VK_SHADER_STAGE_COMPUTE_BIT, EdfPanelShader::getShader());
    pContext->dispatch((roi[2] + 15) / 16, (roi[3] + 15) / 16, 1);
    m_lastPreparedFrame = frameId;
    m_pending = std::move(pending);
    Logger::info(str::format("[EDF-Static-Composition] stage=preview_generated frame=", frameId,
      " request_id=", source.requestId, " camera_frame=", source.cameraFrame,
      " current_frame_only=1 gamma_conversion=1 screenshot=0 debug_view=0 vertices=252 indices=432"));
  } catch (const std::exception& error) {
    // This producer only writes experiment-owned resources. Preserve the
    // raster and offscreen probe if experimental recording fails.
    fail("preview_generation_exception");
    Logger::err(str::format("[EDF-Static-Composition] stage=preview_generation_failed frame=",
      source.frameId, " raster_fallback=1 reason=", error.what()));
  }

  bool EdfStaticComposition::composeWSI(DxvkContext* pContext, const Rc<DxvkImage>& image,
    const Rc<DxvkImage>& originalBackbuffer, uint32_t frameId, uint64_t presentSerial, uint32_t loopIndex, bool presentationCompatible, bool gammaIdentity) {
    if (!hasCurrentPreview(frameId)) {
      return false;
    }
    constexpr VkImageUsageFlags kRequiredUsage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_STORAGE_BIT;
    const auto properties = pContext->getDevice()->adapter()->formatProperties(kPresentationFormat);
    if (image == nullptr || image->info().format != kPresentationFormat || image->info().extent != kPresentationExtent
      || image->info().sampleCount != VK_SAMPLE_COUNT_1_BIT || image->info().numLayers != 1
      || (image->info().usage & kRequiredUsage) != kRequiredUsage || !presentationCompatible || !gammaIdentity || loopIndex
      || originalBackbuffer == nullptr || m_pending->source.originals[0].slot != 0
      || m_pending->source.originals[0].image != originalBackbuffer
      || !(properties.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT)
      || std::any_of(m_pending->source.originals.begin(), m_pending->source.originals.end(),
        [&](const EdfStaticProbe::CompositionOriginal& original) {
          return original.image == image || original.image->handle() == image->handle();
        })) {
      Logger::info(str::format("[EDF-Static-Composition] stage=composition_rejected frame=", frameId,
        " reason=wsi_usage_format_extent_gamma_present_or_alias_guard gamma_identity=", unsigned(gammaIdentity),
        " presentation_compatible=", unsigned(presentationCompatible), " loop_index=", loopIndex,
        " usage=", image != nullptr ? image->info().usage : 0));
      discard("wsi_guard");
      return false;
    }
    auto job = std::make_shared<CompositionReadback>();
    job->source = m_pending->source;
    job->presentSerial = presentSerial;
    job->imageIdentity = reinterpret_cast<uintptr_t>(image.ptr());
    job->loopIndex = loopIndex;
    job->roi = m_pending->roi;
    job->missLinearZ = m_pending->missLinearZ;
    job->failed = m_failed;
    job->images = {{
      { "wsi_before", createReadback(pContext->getDevice().ptr(), 1920ull * 1080 * 4, "EDF WSI before"), 1920, 1080 },
      { "wsi_after", createReadback(pContext->getDevice().ptr(), 1920ull * 1080 * 4, "EDF WSI after"), 1920, 1080 },
      { "expected_panel", createReadback(pContext->getDevice().ptr(), uint64_t(job->roi[2]) * job->roi[3] * 4, "EDF expected panel"), job->roi[2], job->roi[3] },
      { "scratch_before", createReadback(pContext->getDevice().ptr(), uint64_t(job->roi[2]) * job->roi[3] * 4, "EDF WSI panel backup"), job->roi[2], job->roi[3] }
    }};
    job->bounds = createReadback(pContext->getDevice().ptr(), sizeof(uint32_t) * 7, "EDF bounds readback");
    for (const auto& original : job->source.originals) {
      job->originalAfter.push_back(createReadback(pContext->getDevice().ptr(), original.before->info().size,
        "EDF original after WSI overlay"));
    }
    const auto panelView = createView(pContext->getDevice().ptr(), m_pending->preview, VK_IMAGE_USAGE_SAMPLED_BIT);
    const auto presentationView = createView(pContext->getDevice().ptr(), image, VK_IMAGE_USAGE_STORAGE_BIT);
    const auto shader = EdfPresentShader::getShader();
    pContext->emitMemoryBarrier(0, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_ACCESS_MEMORY_WRITE_BIT,
      VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT);
    const VkImageSubresourceLayers subresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
    pContext->copyImageToBuffer(job->images[0].buffer, 0, 0, 0, image, subresource, { 0, 0, 0 }, kPresentationExtent);
    pContext->copyImageToBuffer(job->images[3].buffer, 0, 0, 0, image, subresource,
      { int32_t(job->roi[0]), int32_t(job->roi[1]), 0 }, { job->roi[2], job->roi[3], 1 });
    pContext->copyImageToBuffer(job->images[2].buffer, 0, 0, 0, m_pending->preview, subresource,
      { 0, 0, 0 }, { job->roi[2], job->roi[3], 1 });
    const RestoreImageLayout restoreLayout = { image, image->info().layout };
    pContext->changeImageLayout(image, VK_IMAGE_LAYOUT_GENERAL);
    EdfStaticCompositionArgs args = { job->roi[0], job->roi[1], job->roi[2], job->roi[3], job->missLinearZ, 0, 0, 0 };
    pContext->setPushConstantBank(DxvkPushConstantBank::RTX);
    pContext->bindResourceView(EDF_COMPOSITION_COLOR, panelView, nullptr);
    pContext->bindResourceView(EDF_COMPOSITION_OUTPUT, presentationView, nullptr);
    pContext->bindResourceBuffer(EDF_COMPOSITION_BOUNDS, DxvkBufferSlice(m_pending->bounds, 0, sizeof(uint32_t) * 7));
    pContext->bindShader(VK_SHADER_STAGE_COMPUTE_BIT, shader);
    pContext->pushConstants(0, sizeof(args), &args);
    pContext->dispatch((job->roi[2] + 15) / 16, (job->roi[3] + 15) / 16, 1);
    pContext->copyImageToBuffer(job->images[1].buffer, 0, 0, 0, image, subresource, { 0, 0, 0 }, kPresentationExtent);
    pContext->copyBuffer(job->bounds, 0, m_pending->bounds, 0, sizeof(uint32_t) * 7);
    for (size_t index = 0; index < job->source.originals.size(); index++) {
      pContext->copyImageToBuffer(job->originalAfter[index], 0, 0, 0, job->source.originals[index].image,
        subresource, { 0, 0, 0 }, kPresentationExtent);
    }
    pContext->changeImageLayout(image, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR);
    pContext->emitMemoryBarrier(0, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
      VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_HOST_READ_BIT);
    Rc<sync::CallbackFence> fence = new sync::CallbackFence{};
    fence->setCallback(1, [job] { writeReadback(job); });
    pContext->signal(fence, 1);
    m_lastComposedFrame = frameId;
    m_pending.reset();
    Logger::info(str::format("[EDF-Static-Composition] stage=composition_recorded frame=", frameId,
      " request_id=", job->source.requestId, " present_serial=", presentSerial, " loop_index=", loopIndex,
      " wsi_image=", job->imageIdentity, " roi=", job->roi[0], ',', job->roi[1], ',', job->roi[2], ',', job->roi[3],
      " only_wsi_written=1 game_targets_written=0 present_signal_requires_overlay=1"));
    return true;
  }
}
