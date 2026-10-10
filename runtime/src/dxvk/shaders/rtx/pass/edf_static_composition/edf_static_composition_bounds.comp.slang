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
#include "rtx/pass/edf_static_composition/edf_static_composition.h"

[[vk::binding(EDF_COMPOSITION_DEPTH)]]
Texture2D<float> LinearZ;
[[vk::binding(EDF_COMPOSITION_COLOR)]]
Texture2D<float4> DisplayColor;
[[vk::binding(EDF_COMPOSITION_BOUNDS)]]
RWStructuredBuffer<uint> Bounds;
layout(push_constant)
ConstantBuffer<EdfStaticCompositionArgs> cb;

[shader("compute")]
[numthreads(16, 16, 1)]
void main(uint2 threadId : SV_DispatchThreadID) {
  if (threadId.x >= cb.sourceWidth || threadId.y >= cb.sourceHeight) {
    return;
  }
  const float z = LinearZ.Load(int3(threadId, 0));
  if (!isfinite(z)) {
    InterlockedAdd(Bounds[5], 1);
  } else if (z != cb.missLinearZ) {
    const float3 color = DisplayColor.Load(int3(threadId, 0)).xyz;
    if (!all(isfinite(color))) {
      InterlockedAdd(Bounds[6], 1);
    }
    InterlockedMin(Bounds[0], threadId.x);
    InterlockedMin(Bounds[1], threadId.y);
    InterlockedMax(Bounds[2], threadId.x);
    InterlockedMax(Bounds[3], threadId.y);
    InterlockedAdd(Bounds[4], 1);
  }
}
