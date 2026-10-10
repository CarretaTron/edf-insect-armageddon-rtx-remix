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

[[vk::binding(EDF_COMPOSITION_COLOR)]]
Texture2D<float4> Panel;
[[vk::binding(EDF_COMPOSITION_BOUNDS)]]
StructuredBuffer<uint> Bounds;
[[vk::binding(EDF_COMPOSITION_OUTPUT)]]
layout(rgba8) RWTexture2D<float4> Presentation;
layout(push_constant)
ConstantBuffer<EdfStaticCompositionArgs> cb;

[shader("compute")]
[numthreads(16, 16, 1)]
void main(uint2 threadId : SV_DispatchThreadID) {
  if (threadId.x >= cb.panelWidth || threadId.y >= cb.panelHeight
    || Bounds[4] == 0 || Bounds[5] != 0 || Bounds[6] != 0) {
    return;
  }
  Presentation[threadId + uint2(cb.sourceWidth, cb.sourceHeight)] = Panel.Load(int3(threadId, 0));
}
