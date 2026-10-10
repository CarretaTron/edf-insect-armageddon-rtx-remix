#include <atomic>
#include <cassert>
#include <iostream>
#include <thread>
#include <vector>

#include "rtx_render/rtx_compatibility_trace.h"

int main() {
  using namespace dxvk;
  constexpr uint64_t id = 0x45444650;
  assert(!hasCompatibilityDrawTraces());
  assert(!shouldTraceCompatibilityDraw(0, CompatibilityTraceStage::Recognized, 0));
  assert(!shouldTraceCompatibilityDraw(id, CompatibilityTraceStage(-1), 0));
  assert(!hasCompatibilityDrawTraces());
  for (uint32_t frame = 0; frame < 8; ++frame) {
    assert(shouldTraceCompatibilityDraw(id, CompatibilityTraceStage::Recognized, frame));
    assert(!shouldTraceCompatibilityDraw(id, CompatibilityTraceStage::Recognized, frame));
  }
  assert(hasCompatibilityDrawTraces());
  assert(!shouldTraceCompatibilityDraw(id, CompatibilityTraceStage::Recognized, 126));
  assert(shouldTraceCompatibilityDraw(id, CompatibilityTraceStage::Recognized, 127));
  assert(shouldTraceCompatibilityDraw(id, CompatibilityTraceStage::SceneGeometry, 127));
  assert(shouldTraceCompatibilityDraw(id + 1, CompatibilityTraceStage::Recognized, 127));

  std::atomic<uint32_t> accepted { 0 };
  std::vector<std::thread> threads;
  for (uint32_t i = 0; i < 16; ++i) {
    threads.emplace_back([&] {
      if (shouldTraceCompatibilityDraw(id, CompatibilityTraceStage::TlasRecorded, 133)) {
        ++accepted;
      }
    });
  }
  for (auto& thread : threads) {
    thread.join();
  }
  assert(accepted == 1);
  std::cout << "{\"disabled_marker\":true,\"invalid_stage\":true,\"per_frame_deduplication\":true,"
    "\"initial_frames\":8,\"subsequent_interval\":120,\"independent_stages_and_ids\":true,"
    "\"concurrent_callers\":16,\"concurrent_logs\":1}\n";
}
