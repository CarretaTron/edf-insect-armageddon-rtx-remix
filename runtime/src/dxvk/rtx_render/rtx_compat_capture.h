/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "rtx_option.h"
#include "../../d3d9/d3d9_rtx.h"
#include "../../util/util_compat_capture.h"

namespace dxvk {
  class D3D9DeviceEx;
  class D3D9CommonBuffer;
  class D3D9CommonShader;
  class D3D9CommonTexture;
  class D3D9Surface;

  // This observer never changes the draw state submitted by the application.
  class D3D9CompatibilityCapture {
  public:
    RTX_OPTION("rtx.compatCapture", bool, enabled, false, "Enable bounded D3D9 diagnostic captures for the selected executable.");
    RTX_OPTION("rtx.compatCapture", std::string, executable, "EDF-IA.exe", "Executable allowed to use the diagnostic recorder. Matching is case insensitive.");
    RTX_OPTION("rtx.compatCapture", std::string, outputDirectory, "edf-captures", "Diagnostic output directory, relative to the game's working directory or absolute.");
    RTX_OPTION("rtx.compatCapture", int32_t, startFrame, -1, "Zero-based D3D9 frame to capture automatically. -1: wait for the capture hotkey.");
    RTX_OPTION("rtx.compatCapture", uint32_t, frameCount, 1, "Number of frames per diagnostic capture, clamped to 1 through 3.");
    RTX_OPTION("rtx.compatCapture", uint32_t, hotkey, 121, "Windows virtual key to capture the next frame. 0: disabled. 121: F10.");
    RTX_OPTION("rtx.compatCapture", uint32_t, firstDraw, 0, "First zero-based draw to include in each captured frame.");
    RTX_OPTION("rtx.compatCapture", uint32_t, lastDraw, UINT32_MAX, "Last zero-based draw to include in each captured frame.");
    RTX_OPTION("rtx.compatCapture", std::string, vertexShaderHash, "", "Optional SHA1 filter for the original vertex shader bytecode.");
    RTX_OPTION("rtx.compatCapture", std::string, pixelShaderHash, "", "Optional SHA1 filter for the original pixel shader bytecode.");
    RTX_OPTION("rtx.compatCapture", std::string, renderTargetHash, "", "Optional hexadecimal descriptor hash filter for render target zero.");
    RTX_OPTION("rtx.compatCapture", uint32_t, maxCaptureMiB, 256, "Maximum total diagnostic metadata and blob bytes per capture.");
    RTX_OPTION("rtx.compatCapture", uint32_t, maxQueueMiB, 16, "Maximum queued diagnostic payload memory including the in-flight write, clamped to 1 through 256 MiB.");
    RTX_OPTION("rtx.compatCapture", bool, dumpBuffers, true, "Save CPU-visible vertex and index ranges used by selected draws.");
    RTX_OPTION("rtx.compatCapture", bool, dumpShaders, true, "Save original shader bytecode and disassembly for selected draws.");

    static bool shouldCaptureExecutable();
    explicit D3D9CompatibilityCapture(D3D9DeviceEx* pParent);
    ~D3D9CompatibilityCapture();

    void event(const char* pApi, const std::string& arguments = "{}", bool recorded = false);
    void dataEvent(const char* pApi, const std::string& arguments,
      const void* pData, size_t size, bool recorded);
    void bindingEvent(const char* pApi, const void* pResource, const std::string& arguments, bool recorded);
    void shaderEvent(const char* pApi, const D3D9CommonShader* pShader, const char* pStage, bool recorded);
    void resourceDestroyed(const void* pResource);
    void captureDraw(const char* pApi, const D3D9Rtx::DrawContext& draw,
      const void* pUpVertices = nullptr, uint32_t upStride = 0,
      const void* pUpIndices = nullptr, D3DFORMAT upIndexFormat = D3DFMT_UNKNOWN);
    void drawResult(uint32_t flags);
    void decision(const char* pReason);
    void onPresent();
    void bufferLock(D3D9CommonBuffer* pBuffer, uint32_t offset, uint32_t size, uint32_t flags);
    void bufferUnlock(D3D9CommonBuffer* pBuffer);

  private:
    struct BufferVersion {
      uint64_t version = 0;
      bool pendingWrite = false;
    };

    void beginCapture();
    void endCapture(const char* pReason = "observer_stopped");
    std::string shaderState(const D3D9CommonShader* pShader, const char* pStage);
    std::string textureState(D3D9CommonTexture* pTexture);
    std::string surfaceState(D3D9Surface* pSurface);
    std::string blobState(const void* pData, size_t size, uint64_t byteOffset = 0);
    std::string constantsState(const void* pFloats, uint32_t floatCount,
      const void* pInts, uint32_t intCount, const void* pBools, uint32_t boolCount);
    uint64_t resourceId(const void* pObject);

    D3D9DeviceEx* m_pParent;
    std::unique_ptr<CompatibilityCaptureWriter> m_writer;
    std::unordered_map<const void*, uint64_t> m_resourceIds;
    std::unordered_map<D3D9CommonBuffer*, BufferVersion> m_bufferVersions;
    std::unordered_set<std::string> m_shaderRecords;
    std::unordered_set<std::string> m_stateRecords;
    uint64_t m_nextResourceId = 1;
    uint64_t m_frame = 0;
    uint64_t m_draw = 0;
    uint64_t m_lastDraw = 0;
    uint64_t m_captureStart = 0;
    uint64_t m_captureIndex = 0;
    uint64_t m_selectedDraws = 0;
    uint32_t m_completedFrames = 0;
    bool m_lastSelected = false;
    bool m_previousHotkey = false;
    bool m_automaticCaptureDone = false;
  };
}
