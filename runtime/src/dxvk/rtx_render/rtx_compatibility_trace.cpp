/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 * SPDX-License-Identifier: MIT
 */
#include <array>
#include <atomic>
#include <mutex>
#include <unordered_map>

#include "rtx_compatibility_trace.h"

namespace dxvk {

namespace {
std::atomic<bool> s_hasTraces { false };
}

bool hasCompatibilityDrawTraces() {
  return s_hasTraces.load(std::memory_order_relaxed);
}

const char* compatibilityTraceStageName(CompatibilityTraceStage stage) {
  static constexpr const char* names[] = {
    "recognized", "raster_geometry", "camera_accepted", "camera_rejected",
    "scene_geometry", "scene_rejected", "instance_accepted", "instance_preserved",
    "instance_rejected", "blas_recorded", "blas_reused", "blas_rejected",
    "tlas_recorded", "tlas_rejected", "injection_rejected", "scene_only_frame",
  };
  static_assert(std::size(names) == size_t(CompatibilityTraceStage::Count));
  return uint32_t(stage) < uint32_t(CompatibilityTraceStage::Count) ? names[size_t(stage)] : "invalid";
}

bool shouldTraceCompatibilityDraw(uint64_t id, CompatibilityTraceStage stage, uint32_t frame) {
  if (!id || uint32_t(stage) >= uint32_t(CompatibilityTraceStage::Count)) {
    return false;
  }
  s_hasTraces.store(true, std::memory_order_relaxed);

  struct StageState {
    uint32_t lastFrame = 0;
    uint32_t loggedFrames = 0;
  };
  using StageStates = std::array<StageState, size_t(CompatibilityTraceStage::Count)>;
  static std::mutex mutex;
  static std::unordered_map<uint64_t, StageStates> states;
  const std::lock_guard<std::mutex> lock(mutex);
  if (states.size() >= 64 && states.find(id) == states.end()) {
    return false;
  }

  StageState& state = states[id][size_t(stage)];
  if (state.loggedFrames && (state.lastFrame == frame ||
      (state.loggedFrames >= 8 && uint32_t(frame - state.lastFrame) < 120))) {
    return false;
  }
  state.lastFrame = frame;
  ++state.loggedFrames;
  return true;
}

}
