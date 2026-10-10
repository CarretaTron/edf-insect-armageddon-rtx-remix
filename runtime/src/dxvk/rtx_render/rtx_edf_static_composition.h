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
#pragma once

#include <array>
#include <atomic>
#include <memory>

#include "rtx_edf_static_probe.h"

namespace dxvk {
  class DxvkContext;
  class RtxContext;

  // The producer and consumer run on the device CS thread.
  class EdfStaticComposition {
  public:
    RTX_OPTION("rtx.edfStatic.composition", bool, enabled, false,
      "Allow one manually requested current-frame RTX panel in the WSI image. Game render targets are never written.");
    RTX_OPTION("rtx.edfStatic.composition", uint32_t, panelX, 1040, "Diagnostic panel left coordinate at the required 1920x1080 presentation resolution.");
    RTX_OPTION("rtx.edfStatic.composition", uint32_t, panelY, 800, "Diagnostic panel top coordinate at the required 1920x1080 presentation resolution.");
    RTX_OPTION("rtx.edfStatic.composition", uint32_t, panelWidth, 820, "Diagnostic panel width. Maximum 820.");
    RTX_OPTION("rtx.edfStatic.composition", uint32_t, panelHeight, 200, "Diagnostic panel height. Maximum 200.");

    void preparePreview(RtxContext* pContext, const Resources::RaytracingOutput& output,
      const EdfStaticProbe::CompositionSource& source, bool performSRGBConversion,
      bool captureScreenImage, uint32_t debugViewIndex);
    bool hasCurrentPreview(uint32_t frameId);
    bool composeWSI(DxvkContext* pContext, const Rc<DxvkImage>& image, const Rc<DxvkImage>& originalBackbuffer, uint32_t frameId,
      uint64_t presentSerial, uint32_t loopIndex, bool presentationCompatible, bool gammaIdentity);
    void discard(const char* pReason);
    void fail(const char* pReason) { m_failed->store(true); discard(pReason); }

  private:
    struct Pending {
      EdfStaticProbe::CompositionSource source;
      Rc<DxvkImage> preview;
      Rc<DxvkBuffer> bounds;
      std::array<uint32_t, 4> roi = {};
      float missLinearZ = 0;
    };
    std::unique_ptr<Pending> m_pending;
    uint32_t m_lastPreparedFrame = kInvalidFrameIndex;
    uint32_t m_lastComposedFrame = kInvalidFrameIndex;
    std::shared_ptr<std::atomic<bool>> m_failed = std::make_shared<std::atomic<bool>>(false);
  };
}
