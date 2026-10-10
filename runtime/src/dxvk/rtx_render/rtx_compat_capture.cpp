/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 * SPDX-License-Identifier: MIT
 */
#include <algorithm>
#include <chrono>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <sstream>

#include "rtx_compat_capture.h"
#include "../../d3d9/d3d9_device.h"
#include "../../d3d9/d3d9_texture.h"
#include "../../util/util_env.h"
#include "../../util/util_string.h"

namespace dxvk {
  namespace {
    std::string jsonQuote(const std::string& value) {
      return "\"" + CompatibilityCaptureWriter::jsonEscape(value) + "\"";
    }
    std::string hex64(uint64_t value) {
      std::ostringstream out;
      out << std::hex << std::setfill('0') << std::setw(16) << value;
      return out.str();
    }
    template<typename T>
    std::string integers(const T& values) {
      std::string result = "[";
      for (const auto& value : values) {
        if (result.size() > 1) { result += ","; }
        result += std::to_string(value);
      }
      return result + "]";
    }
    std::string floats(const float* pValues, size_t count) {
      std::ostringstream out;
      out.imbue(std::locale::classic());
      out << "[" << std::setprecision(9);
      for (size_t i = 0; i < count; ++i) {
        if (i) { out << ","; }
        if (std::isfinite(pValues[i])) { out << pValues[i]; }
        else { out << "null"; }
      }
      out << "]";
      return out.str();
    }
    uint64_t elementCount(const D3D9Rtx::DrawContext& draw) {
      const uint64_t count = draw.PrimitiveCount;
      switch (draw.PrimitiveType) {
      case D3DPT_POINTLIST: return count;
      case D3DPT_LINELIST: return count * 2;
      case D3DPT_LINESTRIP: return count + 1;
      case D3DPT_TRIANGLELIST: return count * 3;
      case D3DPT_TRIANGLESTRIP:
      case D3DPT_TRIANGLEFAN: return count + 2;
      default: return 0;
      }
    }
    std::string signature(const DxsoIsgn& input) {
      std::string result = "[";
      for (uint32_t i = 0; i < input.elemCount; ++i) {
        const auto& element = input.elems[i];
        if (i) { result += ","; }
        result += "{\"register\":" + std::to_string(element.regNumber)
          + ",\"usage\":" + std::to_string(uint32_t(element.semantic.usage))
          + ",\"usageIndex\":" + std::to_string(element.semantic.usageIndex) + "}";
      }
      return result + "]";
    }
  }

  bool D3D9CompatibilityCapture::shouldCaptureExecutable() {
    if (!enabled()) { return false; }
    auto actual = env::getExeName();
    auto expected = executable();
    const auto lower = [](unsigned char c) { return char(std::tolower(c)); };
    std::transform(actual.begin(), actual.end(), actual.begin(), lower);
    std::transform(expected.begin(), expected.end(), expected.begin(), lower);
    return !expected.empty() && actual == expected;
  }

  D3D9CompatibilityCapture::D3D9CompatibilityCapture(D3D9DeviceEx* pParent)
    : m_pParent(pParent) {
    if (startFrame() == 0) {
      beginCapture();
      m_automaticCaptureDone = true;
    }
    Logger::info(str::format("Compatibility capture enabled for ", env::getExeName(), "; F10 captures the next frame (configurable)."));
  }

  D3D9CompatibilityCapture::~D3D9CompatibilityCapture() {
    endCapture("device_destroyed");
  }

  void D3D9CompatibilityCapture::beginCapture() {
    try {
      const auto stamp = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
      const auto path = std::filesystem::path(outputDirectory()) /
        (std::to_string(stamp) + "-" + std::to_string(GetCurrentProcessId()) + "-" + std::to_string(m_captureIndex++));
      m_writer = std::make_unique<CompatibilityCaptureWriter>(path,
        uint64_t(std::clamp(maxCaptureMiB(), 1u, 4096u)) * 1024 * 1024,
        uint64_t(std::clamp(maxQueueMiB(), 1u, 256u)) * 1024 * 1024);
      m_captureStart = m_frame;
      m_selectedDraws = 0;
      m_completedFrames = 0;
      m_shaderRecords.clear();
      m_stateRecords.clear();
      m_writer->writeRecord("{\"type\":\"capture_begin\",\"schema\":1,\"frame\":" + std::to_string(m_frame)
        + ",\"revision\":\"0e18e55c04309c08e6206e768bc72b24ac83f853+compat-capture\",\"executable\":"
        + jsonQuote(env::getExePath()) + ",\"hashAlgorithm\":\"sha1\",\"textureContentCaptured\":false} ");
      Logger::info(str::format("Compatibility capture started: ", path.string()));
    } catch (const std::exception& error) {
      Logger::err(str::format("Compatibility capture could not start: ", error.what()));
      m_writer.reset();
    }
  }

  void D3D9CompatibilityCapture::endCapture(const char* pReason) {
    if (!m_writer) { return; }
    try {
      m_writer->writeRecord("{\"type\":\"capture_end\",\"reason\":" + jsonQuote(pReason)
        + ",\"completedFrames\":" + std::to_string(m_completedFrames)
        + ",\"selectedDraws\":" + std::to_string(m_selectedDraws) + "}");
      m_writer->finish();
      if (m_writer->hasError()) { Logger::err(str::format("Compatibility capture: ", m_writer->errorMessage())); }
      Logger::info(str::format("Compatibility capture finished; selected draws: ", m_selectedDraws,
        "; truncated: ", m_writer->isTruncated()));
    } catch (const std::exception& error) {
      Logger::err(str::format("Compatibility capture finish failed: ", error.what()));
    }
    m_writer.reset();
    m_lastSelected = false;
  }

  void D3D9CompatibilityCapture::event(const char* pApi, const std::string& arguments, bool recorded) {
    if (!m_writer) { return; }
    try {
      m_writer->writeRecord("{\"type\":\"event\",\"frame\":" + std::to_string(m_frame)
        + ",\"beforeDraw\":" + std::to_string(m_draw) + ",\"api\":" + jsonQuote(pApi)
        + ",\"recordedInStateBlock\":" + (recorded ? "true" : "false") + ",\"args\":" + arguments + "}");
    } catch (const std::exception& error) {
      Logger::err(str::format("Compatibility capture event failed: ", error.what()));
      endCapture();
    }
  }

  uint64_t D3D9CompatibilityCapture::resourceId(const void* pResource) {
    if (!pResource) { return 0; }
    auto [it, inserted] = m_resourceIds.emplace(pResource, m_nextResourceId);
    if (inserted) { ++m_nextResourceId; }
    return it->second;
  }

  void D3D9CompatibilityCapture::resourceDestroyed(const void* pResource) {
    m_resourceIds.erase(pResource);
    m_bufferVersions.erase(static_cast<D3D9CommonBuffer*>(const_cast<void*>(pResource)));
  }

  void D3D9CompatibilityCapture::dataEvent(const char* pApi, const std::string& arguments,
    const void* pData, size_t size, bool recorded) {
    if (!m_writer) { return; }
    try {
      std::string args = arguments;
      args.pop_back();
      event(pApi, args + (args.size() > 1 ? "," : "") + "\"data\":" + blobState(pData, size) + "}", recorded);
    } catch (const std::exception&) { endCapture(); }
  }

  void D3D9CompatibilityCapture::bindingEvent(const char* pApi, const void* pResource,
    const std::string& arguments, bool recorded) {
    if (!m_writer) { return; }
    try {
      std::string args = arguments;
      args.pop_back();
      event(pApi, args + (args.size() > 1 ? "," : "") + "\"resource\":" + std::to_string(resourceId(pResource)) + "}", recorded);
    } catch (const std::exception&) { endCapture(); }
  }

  void D3D9CompatibilityCapture::shaderEvent(const char* pApi, const D3D9CommonShader* pShader,
    const char* pStage, bool recorded) {
    if (!m_writer) { return; }
    try { event(pApi, "{\"shader\":" + shaderState(pShader, pStage) + "}", recorded); }
    catch (const std::exception&) { endCapture(); }
  }

  std::string D3D9CompatibilityCapture::blobState(const void* pData, size_t bytes, uint64_t byteOffset) {
    if (!pData) { return "{\"available\":false,\"reason\":\"not_cpu_mapped\"}"; }
    const auto blob = m_writer->storeBlob(pData, bytes);
    return "{\"hash\":" + jsonQuote(blob.hash) + ",\"path\":" + jsonQuote(blob.path)
      + ",\"size\":" + std::to_string(blob.size) + ",\"byteOffset\":" + std::to_string(byteOffset)
      + ",\"available\":" + (blob.available ? "true" : "false") + "}";
  }

  std::string D3D9CompatibilityCapture::constantsState(const void* pFloats, uint32_t floatCount,
    const void* pInts, uint32_t intCount, const void* pBools, uint32_t boolCount) {
    auto withCount = [](std::string blob, uint32_t count, bool packed) {
      blob.pop_back();
      return blob + ",\"firstRegister\":0,\"count\":" + std::to_string(count)
        + ",\"packed\":" + (packed ? "true" : "false") + "}";
    };
    return "{\"float\":" + withCount(blobState(pFloats, size_t(floatCount) * 16), floatCount, false)
      + ",\"int\":" + withCount(blobState(pInts, size_t(intCount) * 16), intCount, false)
      + ",\"bool\":" + withCount(blobState(pBools, size_t((boolCount + 31) / 32) * 4), boolCount, true) + "}";
  }

  std::string D3D9CompatibilityCapture::shaderState(const D3D9CommonShader* pShader, const char* pStage) {
    if (!pShader) { return "null"; }
    const auto& bytecode = pShader->GetBytecode();
    const auto hash = CompatibilityCaptureWriter::hashBytes(bytecode.data(), bytecode.size());
    if (m_shaderRecords.insert(std::string(pStage) + hash).second) {
      std::string disassembly = "{\"available\":false,\"reason\":\"disabled_or_disassembly_failed\"}";
      Com<ID3DBlob> text;
      if (dumpShaders() && SUCCEEDED(DisassembleShader(bytecode.data(), FALSE, nullptr, &text))) {
        disassembly = blobState(text->GetBufferPointer(), text->GetBufferSize());
      }
      std::string defs = "[";
      for (const auto& constant : pShader->GetConstants()) {
        if (defs.size() > 1) { defs += ","; }
        defs += "{\"register\":" + std::to_string(constant.uboIdx)
          + ",\"value\":" + floats(constant.float32, 4) + "}";
      }
      defs += "]";
      const auto& meta = pShader->GetMeta();
      const auto mask = pShader->GetShaderMask();
      m_writer->writeRecord("{\"type\":\"shader\",\"hash\":" + jsonQuote(hash) + ",\"stage\":" + jsonQuote(pStage)
        + ",\"name\":" + jsonQuote(pShader->GetName()) + ",\"major\":" + std::to_string(pShader->GetInfo().majorVersion())
        + ",\"minor\":" + std::to_string(pShader->GetInfo().minorVersion()) + ",\"bytecode\":"
        + (dumpShaders() ? blobState(bytecode.data(), bytecode.size()) : "{\"available\":false,\"reason\":\"disabled\"}")
        + ",\"disassembly\":" + disassembly + ",\"inputSignature\":" + signature(pShader->GetIsgn())
        + ",\"outputSignature\":" + signature(pShader->GetOsgn()) + ",\"definedConstants\":" + defs
        + ",\"maxConstantIndexF\":" + std::to_string(meta.maxConstIndexF)
        + ",\"maxConstantIndexI\":" + std::to_string(meta.maxConstIndexI)
        + ",\"maxConstantIndexB\":" + std::to_string(meta.maxConstIndexB)
        + ",\"samplerMask\":" + std::to_string(mask.samplerMask) + ",\"renderTargetMask\":" + std::to_string(mask.rtMask)
        + ",\"relativeConstantCopies\":" + (meta.needsConstantCopies ? "true" : "false") + "}");
    }
    return "{\"hash\":" + jsonQuote(hash) + ",\"stage\":" + jsonQuote(pStage) + "}";
  }

  std::string D3D9CompatibilityCapture::textureState(D3D9CommonTexture* pTexture) {
    if (!pTexture) { return "null"; }
    const auto* pDesc = pTexture->Desc();
    return "{\"resource\":" + std::to_string(resourceId(pTexture))
      + ",\"descriptorHash\":" + jsonQuote(hex64(pDesc->CalculateHash()))
      + ",\"width\":" + std::to_string(pDesc->Width) + ",\"height\":" + std::to_string(pDesc->Height)
      + ",\"depth\":" + std::to_string(pDesc->Depth) + ",\"format\":" + std::to_string(uint32_t(pDesc->Format))
      + ",\"usage\":" + std::to_string(pDesc->Usage) + ",\"pool\":" + std::to_string(pDesc->Pool)
      + ",\"mipLevels\":" + std::to_string(pDesc->MipLevels) + ",\"arraySize\":" + std::to_string(pDesc->ArraySize)
      + ",\"backBuffer\":" + (pDesc->IsBackBuffer ? "true" : "false")
      + ",\"content\":{\"available\":false,\"reason\":\"gpu_texture_readback_not_implemented\"}}";
  }

  std::string D3D9CompatibilityCapture::surfaceState(D3D9Surface* pSurface) {
    if (!pSurface) { return "null"; }
    auto result = textureState(pSurface->GetCommonTexture());
    result.pop_back();
    const auto extent = pSurface->GetSurfaceExtent();
    return result + ",\"mipLevel\":" + std::to_string(pSurface->GetMipLevel())
      + ",\"face\":" + std::to_string(pSurface->GetFace())
      + ",\"surfaceWidth\":" + std::to_string(extent.width)
      + ",\"surfaceHeight\":" + std::to_string(extent.height) + "}";
  }

  void D3D9CompatibilityCapture::captureDraw(const char* pApi, const D3D9Rtx::DrawContext& draw,
    const void* pUpVertices, uint32_t upStride, const void* pUpIndices, D3DFORMAT upIndexFormat) {
    m_lastDraw = m_draw++;
    m_lastSelected = false;
    if (!m_writer || m_lastDraw < firstDraw() || m_lastDraw > lastDraw()) { return; }
    try {
      const auto& state = m_pParent->m_state;
      const auto* pVs = state.vertexShader != nullptr ? state.vertexShader->GetCommonShader() : nullptr;
      const auto* pPs = state.pixelShader != nullptr ? state.pixelShader->GetCommonShader() : nullptr;
      auto matches = [](const D3D9CommonShader* pShader, const std::string& filter) {
        if (filter.empty()) { return true; }
        if (!pShader) { return false; }
        const auto& bytes = pShader->GetBytecode();
        return CompatibilityCaptureWriter::hashBytes(bytes.data(), bytes.size()) == filter;
      };
      if (!matches(pVs, vertexShaderHash()) || !matches(pPs, pixelShaderHash())) { return; }
      if (!renderTargetHash().empty() && (state.renderTargets[0] == nullptr
        || hex64(state.renderTargets[0]->GetCommonTexture()->Desc()->CalculateHash()) != renderTargetHash())) { return; }

      const bool up = pUpVertices != nullptr;
      const uint64_t count = elementCount(draw);
      uint64_t minIndex = 0, maxIndex = count ? count - 1 : 0;
      bool validRange = count != 0;
      std::string indices = "null";
      if (draw.Indexed) {
        auto* pBuffer = up ? nullptr : GetCommonBuffer(state.indices);
        const auto format = up ? upIndexFormat : (pBuffer ? D3DFORMAT(pBuffer->Desc()->Format) : D3DFMT_UNKNOWN);
        const uint64_t indexStride = format == D3DFMT_INDEX16 ? 2 : (format == D3DFMT_INDEX32 ? 4 : 0);
        const uint64_t offset = uint64_t(draw.StartIndex) * indexStride;
        const uint64_t size = count * indexStride;
        const uint8_t* pData = up ? static_cast<const uint8_t*>(pUpIndices) : nullptr;
        validRange = validRange && indexStride != 0;
        if (pBuffer) {
          const auto mapping = pBuffer->GetMappedSlice();
          const uint64_t available = std::min(uint64_t(pBuffer->Desc()->Size), uint64_t(mapping.length));
          validRange = validRange && !pBuffer->WasWrittenByGPU() && !pBuffer->GetLockCount()
            && mapping.mapPtr && offset <= available && size <= available - offset;
          if (validRange) { pData = static_cast<const uint8_t*>(mapping.mapPtr) + offset; }
        }
        validRange = validRange && pData;
        if (validRange) {
          minIndex = UINT32_MAX;
          maxIndex = 0;
          for (uint64_t i = 0; i < count; ++i) {
            uint32_t value = 0;
            std::memcpy(&value, pData + i * indexStride, size_t(indexStride));
            minIndex = std::min(minIndex, uint64_t(value));
            maxIndex = std::max(maxIndex, uint64_t(value));
          }
        }
        indices = "{\"resource\":" + std::to_string(resourceId(pBuffer)) + ",\"version\":"
          + std::to_string(pBuffer ? m_bufferVersions[pBuffer].version : 0) + ",\"format\":" + std::to_string(format)
          + ",\"count\":" + std::to_string(count) + ",\"blob\":"
          + (validRange && dumpBuffers() ? blobState(pData, size_t(size), offset)
            : "{\"available\":false,\"reason\":\"disabled_or_unavailable_index_range\"}") + "}";
      }
      const int64_t firstVertex = int64_t(draw.BaseVertexIndex) + int64_t(minIndex);
      const int64_t lastVertex = int64_t(draw.BaseVertexIndex) + int64_t(maxIndex);
      validRange = validRange && firstVertex >= 0 && lastVertex >= firstVertex;
      std::string streams = "[";
      for (uint32_t i = 0; i < state.vertexBuffers.size(); ++i) {
        if (up && i != 0) { continue; }
        const auto& stream = state.vertexBuffers[i];
        auto* pBuffer = up ? nullptr : GetCommonBuffer(stream.vertexBuffer);
        if ((!up || i != 0) && !pBuffer) { continue; }
        if (streams.size() > 1) { streams += ","; }
        const uint32_t stride = up && i == 0 ? upStride : stream.stride;
        const uint32_t streamOffset = up && i == 0 ? 0 : stream.offset;
        const uint64_t offset = streamOffset + uint64_t(std::max(int64_t(0), firstVertex)) * stride;
        const uint64_t size = validRange ? uint64_t(lastVertex - firstVertex + 1) * stride : 0;
        const uint8_t* pData = nullptr;
        const bool instanceData = !up && (state.streamFreq[i] & D3DSTREAMSOURCE_INSTANCEDATA) != 0;
        std::string reason = instanceData ? "instanced_stream_requires_decoder" : "disabled_or_unavailable_vertex_range";
        if (validRange && !instanceData && stride) {
          if (up && i == 0) {
            const uint64_t availableVertices = draw.Indexed ? uint64_t(draw.MinVertexIndex) + draw.NumVertices : count;
            if (uint64_t(lastVertex) < availableVertices) { pData = static_cast<const uint8_t*>(pUpVertices) + offset; }
          } else if (pBuffer) {
            const auto mapping = pBuffer->GetMappedSlice();
            const uint64_t available = std::min(uint64_t(pBuffer->Desc()->Size), uint64_t(mapping.length));
            if (!pBuffer->WasWrittenByGPU() && !pBuffer->GetLockCount() && mapping.mapPtr
              && offset <= available && size <= available - offset) {
              pData = static_cast<const uint8_t*>(mapping.mapPtr) + offset;
            }
          }
        }
        streams += "{\"stream\":" + std::to_string(i) + ",\"offset\":" + std::to_string(streamOffset)
          + ",\"stride\":" + std::to_string(stride) + ",\"frequency\":" + std::to_string(up ? 1 : state.streamFreq[i])
          + ",\"source\":" + jsonQuote(up ? "up" : "buffer")
          + ",\"resource\":" + std::to_string(resourceId(pBuffer)) + ",\"version\":"
          + std::to_string(pBuffer ? m_bufferVersions[pBuffer].version : 0) + ",\"blob\":"
          + (pData && dumpBuffers() ? blobState(pData, size_t(size), offset)
            : "{\"available\":false,\"reason\":" + jsonQuote(reason) + "}") + "}";
      }
      streams += "]";
      std::string elements = "[";
      if (state.vertexDecl != nullptr) {
        for (const auto& element : state.vertexDecl->GetElements()) {
          if (elements.size() > 1) { elements += ","; }
          elements += "{\"stream\":" + std::to_string(element.Stream) + ",\"offset\":" + std::to_string(element.Offset)
            + ",\"type\":" + std::to_string(element.Type) + ",\"method\":" + std::to_string(element.Method)
            + ",\"usage\":" + std::to_string(element.Usage) + ",\"usageIndex\":" + std::to_string(element.UsageIndex) + "}";
        }
      }
      elements += "]";
      std::string textures = "[", targets = "[", samplers = "[", stages = "[";
      for (uint32_t i = 0; i < state.textures.size(); ++i) {
        if (i) { textures += ","; samplers += ","; }
        textures += textureState(GetCommonTexture(state.textures[i]));
        samplers += integers(state.samplerStates[i]);
      }
      for (const auto& target : state.renderTargets) {
        if (targets.size() > 1) { targets += ","; }
        targets += surfaceState(target.ptr());
      }
      for (const auto& stage : state.textureStages) {
        if (stages.size() > 1) { stages += ","; }
        stages += integers(stage);
      }
      const auto& viewport = state.viewport;
      const auto& scissor = state.scissorRect;
      const auto declarationHash = CompatibilityCaptureWriter::hashBytes(elements.data(), elements.size());
      std::string snapshot = "{\"vertexShader\":" + shaderState(pVs, "vs") + ",\"pixelShader\":" + shaderState(pPs, "ps")
        + ",\"vertexDeclaration\":{\"hash\":" + jsonQuote(declarationHash) + ",\"elements\":" + elements
        + "},\"fvf\":" + std::to_string(state.vertexDecl != nullptr ? state.vertexDecl->GetFVF() : 0)
        + ",\"streams\":" + streams + ",\"indices\":" + indices + ",\"textures\":" + textures + "]"
        + ",\"renderTargets\":" + targets + "]" + ",\"depthStencil\":"
        + surfaceState(state.depthStencil.ptr())
        + ",\"renderStates\":" + integers(state.renderStates) + ",\"samplerStates\":" + samplers + "]"
        + ",\"textureStageStates\":" + stages + "]" + ",\"vsConstants\":"
        + constantsState(state.vsConsts.fConsts, m_pParent->CanSWVP() ? 8192 : 256,
          state.vsConsts.iConsts, m_pParent->CanSWVP() ? 2048 : 16,
          state.vsConsts.bConsts, m_pParent->CanSWVP() ? 2048 : 16)
        + ",\"psConstants\":" + constantsState(state.psConsts.fConsts, 224,
          state.psConsts.iConsts, 16, state.psConsts.bConsts, 16)
        + ",\"transforms\":{\"storage\":\"column_major_float32\",\"all\":"
        + blobState(state.transforms.data(), sizeof(state.transforms))
        + ",\"world\":" + floats(reinterpret_cast<const float*>(&state.transforms[GetTransformIndex(D3DTS_WORLD)]), 16)
        + ",\"view\":" + floats(reinterpret_cast<const float*>(&state.transforms[GetTransformIndex(D3DTS_VIEW)]), 16)
        + ",\"projection\":" + floats(reinterpret_cast<const float*>(&state.transforms[GetTransformIndex(D3DTS_PROJECTION)]), 16) + "}"
        + ",\"viewport\":{\"x\":" + std::to_string(viewport.X) + ",\"y\":" + std::to_string(viewport.Y)
        + ",\"width\":" + std::to_string(viewport.Width) + ",\"height\":" + std::to_string(viewport.Height)
        + ",\"minZ\":" + floats(&viewport.MinZ, 1).substr(1, floats(&viewport.MinZ, 1).size() - 2)
        + ",\"maxZ\":" + floats(&viewport.MaxZ, 1).substr(1, floats(&viewport.MaxZ, 1).size() - 2) + "}"
        + ",\"scissor\":[" + std::to_string(scissor.left) + "," + std::to_string(scissor.top)
        + "," + std::to_string(scissor.right) + "," + std::to_string(scissor.bottom) + "]}";
      const auto stateHash = CompatibilityCaptureWriter::hashBytes(snapshot.data(), snapshot.size());
      if (m_stateRecords.insert(stateHash).second) {
        m_writer->writeRecord("{\"type\":\"state\",\"id\":" + jsonQuote(stateHash) + ",\"state\":" + snapshot + "}");
      }
      m_writer->writeRecord("{\"type\":\"draw\",\"frame\":" + std::to_string(m_frame) + ",\"draw\":" + std::to_string(m_lastDraw)
        + ",\"api\":" + jsonQuote(pApi) + ",\"stateId\":" + jsonQuote(stateHash)
        + ",\"args\":{\"primitiveType\":" + std::to_string(draw.PrimitiveType)
        + ",\"primitiveCount\":" + std::to_string(draw.PrimitiveCount) + ",\"baseVertexIndex\":" + std::to_string(draw.BaseVertexIndex)
        + ",\"startVertex\":" + std::to_string(draw.Indexed ? 0 : draw.BaseVertexIndex)
        + ",\"minVertexIndex\":" + std::to_string(draw.MinVertexIndex) + ",\"numVertices\":" + std::to_string(draw.NumVertices)
        + ",\"startIndex\":" + std::to_string(draw.StartIndex) + ",\"indexed\":" + (draw.Indexed ? "true" : "false")
        + ",\"elementCount\":" + std::to_string(count) + "},\"inputRangeValid\":" + (validRange ? "true" : "false") + "}");
      m_lastSelected = true;
      ++m_selectedDraws;
    } catch (const std::exception& error) {
      Logger::err(str::format("Compatibility capture draw failed: ", error.what()));
      endCapture();
    }
  }

  void D3D9CompatibilityCapture::drawResult(uint32_t flags) {
    if (!m_writer || !m_lastSelected) { return; }
    try {
      m_writer->writeRecord("{\"type\":\"draw_result\",\"frame\":" + std::to_string(m_frame)
        + ",\"draw\":" + std::to_string(m_lastDraw) + ",\"prepareFlags\":" + std::to_string(flags) + "}");
    } catch (const std::exception&) { endCapture(); }
  }

  void D3D9CompatibilityCapture::decision(const char* pReason) {
    if (!m_lastSelected) { return; }
    event("RemixDecision", "{\"draw\":" + std::to_string(m_lastDraw) + ",\"reason\":" + jsonQuote(pReason) + "}");
  }

  void D3D9CompatibilityCapture::onPresent() {
    event("Present", "{\"totalDraws\":" + std::to_string(m_draw) + "}");
    if (m_writer) { ++m_completedFrames; }
    if (m_writer && m_frame - m_captureStart + 1 >= std::clamp(frameCount(), 1u, 3u)) { endCapture("window_complete"); }
    ++m_frame;
    m_draw = 0;
    m_lastSelected = false;
    const bool pressed = hotkey() && (GetAsyncKeyState(int(hotkey())) & 0x8000);
    if (!m_writer && ((!m_automaticCaptureDone && startFrame() >= 0 && uint64_t(startFrame()) == m_frame)
      || (pressed && !m_previousHotkey))) {
      beginCapture();
      m_automaticCaptureDone = true;
    }
    m_previousHotkey = pressed;
  }

  void D3D9CompatibilityCapture::bufferLock(D3D9CommonBuffer* pBuffer, uint32_t offset, uint32_t size, uint32_t flags) {
    try {
      auto& version = m_bufferVersions[pBuffer];
      version.pendingWrite |= (flags & D3DLOCK_READONLY) == 0;
      event("BufferLock", "{\"resource\":" + std::to_string(resourceId(pBuffer)) + ",\"offset\":" + std::to_string(offset)
        + ",\"size\":" + std::to_string(size) + ",\"flags\":" + std::to_string(flags) + "}");
    } catch (const std::exception&) { endCapture(); }
  }

  void D3D9CompatibilityCapture::bufferUnlock(D3D9CommonBuffer* pBuffer) {
    try {
      auto& version = m_bufferVersions[pBuffer];
      if (version.pendingWrite) { ++version.version; version.pendingWrite = false; }
      event("BufferUnlock", "{\"resource\":" + std::to_string(resourceId(pBuffer))
        + ",\"version\":" + std::to_string(version.version) + "}");
    } catch (const std::exception&) { endCapture(); }
  }
}
