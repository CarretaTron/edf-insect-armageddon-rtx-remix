/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <cstdint>
#include <iomanip>
#include <utility>

#include "../../util/log/log.h"
#include "../../util/util_string.h"

namespace dxvk {

enum class CompatibilityTraceStage {
  Recognized,
  RasterGeometry,
  CameraAccepted,
  CameraRejected,
  SceneGeometry,
  SceneRejected,
  InstanceAccepted,
  InstancePreserved,
  InstanceRejected,
  BlasRecorded,
  BlasReused,
  BlasRejected,
  TlasRecorded,
  TlasRejected,
  InjectionRejected,
  SceneOnlyFrame,
  Count,
};

const char* compatibilityTraceStageName(CompatibilityTraceStage stage);
bool hasCompatibilityDrawTraces();
bool shouldTraceCompatibilityDraw(uint64_t id, CompatibilityTraceStage stage, uint32_t frame);

template<typename... Args>
void traceCompatibilityDraw(uint64_t id, CompatibilityTraceStage stage, uint32_t frame, Args&&... args) {
  if (shouldTraceCompatibilityDraw(id, stage, frame)) {
    Logger::info(str::format("[RTX-Compatibility-Trace] trace=0x", std::hex, id, std::dec,
      " stage=", compatibilityTraceStageName(stage), " frame=", frame, " ", std::forward<Args>(args)...));
  }
}

}
