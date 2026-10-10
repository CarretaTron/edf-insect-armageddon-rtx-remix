/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 * SPDX-License-Identifier: MIT
 */

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <regex>
#include <stdexcept>
#include <string>
#include <vector>

#include <windows.h>
#include <d3d9.h>

namespace {
  uint32_t s_checkedCalls = 0;

  void require(bool condition, const char* pMessage) {
    if (!condition) {
      throw std::runtime_error(pMessage);
    }
  }

  void check(HRESULT result, const char* pOperation) {
    s_checkedCalls++;
    if (FAILED(result)) {
      std::cerr << "{\"event\":\"HRESULT_failure\",\"operation\":\"" << pOperation
                << "\",\"hresult\":" << uint32_t(result) << "}\n";
      throw std::runtime_error(pOperation);
    }
  }

  template<typename T>
  class ComPointer {
  public:
    ~ComPointer() {
      reset();
    }
    void reset() {
      if (m_pObject != nullptr) {
        m_pObject->Release();
        m_pObject = nullptr;
      }
    }
    ComPointer() = default;
    ComPointer(const ComPointer&) = delete;
    ComPointer& operator=(const ComPointer&) = delete;
    T* get() const { return m_pObject; }
    T* operator->() const { return m_pObject; }
    T** put() { return &m_pObject; }
  private:
    T* m_pObject = nullptr;
  };

  template<typename T>
  T read(std::ifstream& stream) {
    T result {};
    require(bool(stream.read(reinterpret_cast<char*>(&result), sizeof(result))), "Truncated replay fixture.");
    return result;
  }

  std::vector<uint8_t> readBytes(std::ifstream& stream) {
    const auto length = read<uint32_t>(stream);
    require(length && length < 1024 * 1024, "Invalid replay byte span.");
    std::vector<uint8_t> result(length);
    require(bool(stream.read(reinterpret_cast<char*>(result.data()), length)), "Truncated replay byte span.");
    return result;
  }

  struct CapturedFrame {
    uint32_t originalFrame;
    std::array<std::array<std::vector<uint8_t>, 3>, 2> constants;
    std::vector<uint32_t> renderStates;
    std::array<std::vector<uint32_t>, 3> samplers;
    D3DVIEWPORT9 viewport;
    RECT scissor;
    std::array<D3DMATRIX, 3> transforms;
  };

  struct Fixture {
    std::array<std::vector<uint8_t>, 5> streams;
    std::vector<uint8_t> indices;
    std::vector<uint8_t> vertexShader;
    std::vector<uint8_t> pixelShader;
    std::array<CapturedFrame, 3> frames;
  };

  Fixture readFixture(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    require(bool(stream), "Cannot open replay fixture.");
    char magic[8];
    require(bool(stream.read(magic, 8)) && std::memcmp(magic, "EDFGPU01", 8) == 0, "Incorrect replay magic.");
    Fixture result;
    for (auto& bytes : result.streams) {
      bytes = readBytes(stream);
    }
    result.indices = readBytes(stream);
    result.vertexShader = readBytes(stream);
    result.pixelShader = readBytes(stream);
    require(read<uint32_t>(stream) == result.frames.size(), "Three replay frames are required.");
    for (auto& frame : result.frames) {
      frame.originalFrame = read<uint32_t>(stream);
      for (auto& stage : frame.constants) {
        for (auto& bank : stage) {
          bank = readBytes(stream);
        }
      }
      const uint32_t stateCount = read<uint32_t>(stream);
      require(stateCount <= 512, "Too many replay render states.");
      frame.renderStates.resize(stateCount);
      for (auto& state : frame.renderStates) {
        state = read<uint32_t>(stream);
      }
      require(read<uint32_t>(stream) == frame.samplers.size(), "Three replay samplers are required.");
      for (auto& sampler : frame.samplers) {
        const uint32_t count = read<uint32_t>(stream);
        require(count == 14, "Incorrect replay sampler state count.");
        sampler.resize(count);
        for (auto& value : sampler) {
          value = read<uint32_t>(stream);
        }
      }
      frame.viewport.X = read<uint32_t>(stream);
      frame.viewport.Y = read<uint32_t>(stream);
      frame.viewport.Width = read<uint32_t>(stream);
      frame.viewport.Height = read<uint32_t>(stream);
      frame.viewport.MinZ = read<float>(stream);
      frame.viewport.MaxZ = read<float>(stream);
      frame.scissor.left = read<LONG>(stream);
      frame.scissor.top = read<LONG>(stream);
      frame.scissor.right = read<LONG>(stream);
      frame.scissor.bottom = read<LONG>(stream);
      for (auto& matrix : frame.transforms) {
        require(bool(stream.read(reinterpret_cast<char*>(&matrix), sizeof(matrix))), "Truncated replay transform.");
      }
    }
    require(stream.peek() == std::char_traits<char>::eof(), "Trailing replay fixture bytes.");
    return result;
  }

  LRESULT CALLBACK processWindow(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    return DefWindowProcW(window, message, wParam, lParam);
  }

  class ReplayWindow {
  public:
    ReplayWindow() {
      SetProcessDPIAware();
      WNDCLASSW windowClass {};
      windowClass.lpfnWndProc = processWindow;
      windowClass.hInstance = GetModuleHandleW(nullptr);
      windowClass.lpszClassName = L"EdfStaticGpuReplay";
      require(RegisterClassW(&windowClass) != 0, "RegisterClassW failed.");
      m_window = CreateWindowW(windowClass.lpszClassName, L"EDF static mesh replay (synthetic textures)",
        WS_POPUP, 0, 0, 1920, 1080,
        nullptr, nullptr, windowClass.hInstance, nullptr);
      require(m_window != nullptr, "CreateWindowW failed.");
      require(SetWindowPos(m_window, nullptr, 0, 0, 1920, 1080,
        SWP_NOACTIVATE | SWP_NOZORDER) != 0, "SetWindowPos exact replay extent failed.");
      RECT client {};
      require(GetClientRect(m_window, &client) != 0 && client.right == 1920 && client.bottom == 1080,
        "Replay client extent must be exactly 1920x1080.");
    }
    ~ReplayWindow() {
      DestroyWindow(m_window);
    }
    HWND get() const { return m_window; }
    void pump() const {
      MSG message;
      while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
      }
    }
  private:
    HWND m_window = nullptr;
  };

  struct CallbackCompletion {
    uint32_t probeRecorded = 0;
    uint32_t probeCompleted = 0;
    uint32_t compositionRecorded = 0;
    uint32_t compositionCompleted = 0;
    bool complete() const {
      return probeRecorded == probeCompleted && compositionRecorded == compositionCompleted;
    }
  };

  CallbackCompletion auditCallbackCompletion(const std::filesystem::path& path) {
    require(std::filesystem::is_regular_file(path), "Runtime log is missing while waiting for capture callbacks.");
    require(std::filesystem::file_size(path) <= 64 * 1024 * 1024, "Runtime log exceeds bounded callback audit size.");
    std::ifstream input(path);
    require(bool(input), "Cannot read Runtime callback log.");
    using JobIdentity = std::pair<uint64_t, uint64_t>;
    struct JobCounts { uint32_t recorded = 0; uint32_t completed = 0; };
    std::map<JobIdentity, JobCounts> probes, compositions;
    static const std::regex fatal(
      "SYNC-HAZARD-[A-Z_]+|VUID-[A-Za-z0-9_-]+|VK_ERROR_DEVICE_LOST|"
      "\\b(READ|WRITE|PRESENT)_(AFTER|RACING)_[A-Z_]+\\s+hazard\\s+detected\\b|"
      "\\b(err|error)\\s*:.*?\\[VK_DEBUG_REPORT\\]|"
      "command submission failed|failed to (submit|wait for (a )?fence)|"
      "(vkQueueSubmit|vkQueuePresent|vkWaitForFences|vkGetFenceStatus).*?(failed|error)|"
      "SPIR-V validation failed|shader compilation failed|unhandled exception|"
      "stage=(readback_failed|probe_failed|composition_readback_failed|composition_recording_failed|"
      "composition_failed|preview_generation_failed)", std::regex::icase);
    static const std::regex frameField("\\bframe=([0-9]+)\\b"), requestField("\\brequest_id=([0-9]+)\\b");
    std::string line;
    while (std::getline(input, line)) {
      // The logger appends complete newline-terminated records. Do not count a
      // partially written final line as callback completion.
      if (input.eof()) {
        break;
      }
      if (std::regex_search(line, fatal)) {
        throw std::runtime_error("Runtime/GPU failure during callback completion: " + line);
      }
      const bool probe = line.find("[EDF-Static-Probe]") != std::string::npos;
      const bool composition = line.find("[EDF-Static-Composition]") != std::string::npos;
      const bool recorded = (probe && line.find("stage=readback_recorded ") != std::string::npos)
        || (composition && line.find("stage=composition_recorded ") != std::string::npos);
      const bool completed = (probe && line.find("stage=gpu_completed ") != std::string::npos)
        || (composition && line.find("stage=composition_gpu_completed ") != std::string::npos);
      if (!recorded && !completed) {
        continue;
      }
      std::smatch frameMatch, requestMatch;
      require(std::regex_search(line, frameMatch, frameField) && std::regex_search(line, requestMatch, requestField),
        "Capture callback log lacks frame/request identity.");
      const JobIdentity identity { std::stoull(frameMatch[1].str()), std::stoull(requestMatch[1].str()) };
      auto& job = (probe ? probes : compositions)[identity];
      if (recorded) {
        job.recorded++;
      } else {
        job.completed++;
      }
      require(job.recorded <= 1 && job.completed <= 1 && job.completed <= job.recorded,
        "Unmatched or duplicate capture callback completion.");
    }
    CallbackCompletion result;
    for (const auto& job : probes) {
      result.probeRecorded += job.second.recorded;
      result.probeCompleted += job.second.completed;
    }
    for (const auto& job : compositions) {
      result.compositionRecorded += job.second.recorded;
      result.compositionCompleted += job.second.completed;
    }
    return result;
  }

  void printCallbackCompletion(const CallbackCompletion& result, const char* pEvent) {
    std::cout << "{\"event\":\"" << pEvent << "\",\"probeRecorded\":" << result.probeRecorded
      << ",\"probeCompleted\":" << result.probeCompleted
      << ",\"compositionRecorded\":" << result.compositionRecorded
      << ",\"compositionCompleted\":" << result.compositionCompleted
      << ",\"complete\":" << (result.complete() ? "true" : "false") << "}\n";
  }

  void finishReplay(IDirect3DDevice9* pDevice, const ReplayWindow& window,
      const std::filesystem::path& directory) {
    // A fence alone does not wait for callback CPU file I/O. First drain the
    // current native/Bridge command stream, then require each recorded probe
    // and composition callback to publish its corresponding completion log.
    ComPointer<IDirect3DQuery9> gpuEvent;
    check(pDevice->CreateQuery(D3DQUERYTYPE_EVENT, gpuEvent.put()), "CreateQuery final GPU event");
    check(gpuEvent->Issue(D3DISSUE_END), "Issue final GPU event");
    const ULONGLONG gpuDeadline = GetTickCount64() + 30000;
    for (;;) {
      BOOL completed = FALSE;
      const HRESULT status = gpuEvent->GetData(&completed, sizeof(completed), D3DGETDATA_FLUSH);
      check(status, "GetData final GPU event");
      if (status == S_OK && completed) {
        break;
      }
      require(GetTickCount64() < gpuDeadline, "Timed out waiting for final GPU event.");
      window.pump();
      Sleep(10);
    }
    const ULONGLONG callbackDeadline = GetTickCount64() + 30000;
    for (;;) {
      const auto status = auditCallbackCompletion(directory / "rtx-remix/logs/remix-dxvk.log");
      if (status.complete()) {
        printCallbackCompletion(status, "callbacks_completed");
        break;
      }
      require(GetTickCount64() < callbackDeadline, "Timed out waiting for capture callback metadata publication.");
      window.pump();
      Sleep(10);
    }
  }

  void fillCompressed(const D3DLOCKED_RECT& mapping, uint32_t width, uint32_t height,
      bool dxt5, bool checker) {
    const uint32_t blockSize = dxt5 ? 16 : 8;
    const uint32_t columns = (width + 3) / 4;
    const uint32_t rows = (height + 3) / 4;
    for (uint32_t y = 0; y < rows; y++) {
      for (uint32_t x = 0; x < columns; x++) {
        auto* pBlock = static_cast<uint8_t*>(mapping.pBits) + size_t(y) * mapping.Pitch + x * blockSize;
        std::memset(pBlock, 0, blockSize);
        if (dxt5) {
          pBlock[0] = 255;
        }
        const uint16_t color = checker ? (((x / 4 + y / 4) & 1) ? 0x001f : 0xffff) : 0x4208;
        std::memcpy(pBlock + (dxt5 ? 8 : 0), &color, sizeof(color));
      }
    }
  }

  void fillTexture(IDirect3DTexture9* pTexture) {
    for (uint32_t mip = 0; mip < pTexture->GetLevelCount(); mip++) {
      D3DSURFACE_DESC description {};
      check(pTexture->GetLevelDesc(mip, &description), "GetLevelDesc synthetic base texture");
      D3DLOCKED_RECT mapping {};
      check(pTexture->LockRect(mip, &mapping, nullptr, 0), "LockRect synthetic base texture");
      fillCompressed(mapping, description.Width, description.Height, true, true);
      check(pTexture->UnlockRect(mip), "UnlockRect synthetic base texture");
    }
  }

  void fillCube(IDirect3DCubeTexture9* pTexture) {
    for (uint32_t face = 0; face < 6; face++) {
      for (uint32_t mip = 0; mip < pTexture->GetLevelCount(); mip++) {
        D3DSURFACE_DESC description {};
        check(pTexture->GetLevelDesc(mip, &description), "GetLevelDesc synthetic cube");
        D3DLOCKED_RECT mapping {};
        check(pTexture->LockRect(D3DCUBEMAP_FACES(face), mip, &mapping, nullptr, 0), "LockRect synthetic cube");
        fillCompressed(mapping, description.Width, description.Height, false, false);
        check(pTexture->UnlockRect(D3DCUBEMAP_FACES(face), mip), "UnlockRect synthetic cube");
      }
    }
  }

  void writeVertexBuffer(IDirect3DVertexBuffer9* pBuffer, const std::vector<uint8_t>& bytes, uint32_t stride) {
    void* pMapping = nullptr;
    check(pBuffer->Lock(0, 0, &pMapping, 0), "Lock captured vertex buffer");
    std::memset(pMapping, 0, 882 * stride);
    std::memcpy(static_cast<uint8_t*>(pMapping) + 630 * stride, bytes.data(), bytes.size());
    check(pBuffer->Unlock(), "Unlock captured vertex buffer");
  }

  void setFrameState(IDirect3DDevice9* pDevice, const CapturedFrame& frame) {
    constexpr D3DRENDERSTATETYPE kStates[] = {
      D3DRS_ZENABLE, D3DRS_FILLMODE, D3DRS_SHADEMODE, D3DRS_ZWRITEENABLE, D3DRS_ALPHATESTENABLE,
      D3DRS_LASTPIXEL, D3DRS_SRCBLEND, D3DRS_DESTBLEND, D3DRS_CULLMODE, D3DRS_ZFUNC,
      D3DRS_ALPHAREF, D3DRS_ALPHAFUNC, D3DRS_DITHERENABLE, D3DRS_ALPHABLENDENABLE, D3DRS_FOGENABLE,
      D3DRS_SPECULARENABLE, D3DRS_FOGCOLOR, D3DRS_FOGTABLEMODE, D3DRS_FOGSTART, D3DRS_FOGEND,
      D3DRS_FOGDENSITY, D3DRS_RANGEFOGENABLE, D3DRS_STENCILENABLE, D3DRS_STENCILFAIL, D3DRS_STENCILZFAIL,
      D3DRS_STENCILPASS, D3DRS_STENCILFUNC, D3DRS_STENCILREF, D3DRS_STENCILMASK, D3DRS_STENCILWRITEMASK,
      D3DRS_TEXTUREFACTOR, D3DRS_WRAP0, D3DRS_CLIPPING, D3DRS_LIGHTING, D3DRS_AMBIENT,
      D3DRS_FOGVERTEXMODE, D3DRS_COLORVERTEX, D3DRS_LOCALVIEWER, D3DRS_NORMALIZENORMALS,
      D3DRS_DIFFUSEMATERIALSOURCE, D3DRS_SPECULARMATERIALSOURCE, D3DRS_AMBIENTMATERIALSOURCE,
      D3DRS_EMISSIVEMATERIALSOURCE, D3DRS_VERTEXBLEND, D3DRS_CLIPPLANEENABLE, D3DRS_POINTSIZE,
      D3DRS_POINTSIZE_MIN, D3DRS_POINTSPRITEENABLE, D3DRS_POINTSCALEENABLE, D3DRS_POINTSCALE_A,
      D3DRS_POINTSCALE_B, D3DRS_POINTSCALE_C, D3DRS_MULTISAMPLEANTIALIAS, D3DRS_MULTISAMPLEMASK,
      D3DRS_PATCHEDGESTYLE, D3DRS_POINTSIZE_MAX, D3DRS_INDEXEDVERTEXBLENDENABLE, D3DRS_COLORWRITEENABLE,
      D3DRS_TWEENFACTOR, D3DRS_BLENDOP, D3DRS_POSITIONDEGREE, D3DRS_NORMALDEGREE, D3DRS_SCISSORTESTENABLE,
      D3DRS_SLOPESCALEDEPTHBIAS, D3DRS_ANTIALIASEDLINEENABLE, D3DRS_MINTESSELLATIONLEVEL,
      D3DRS_MAXTESSELLATIONLEVEL, D3DRS_ADAPTIVETESS_X, D3DRS_ADAPTIVETESS_Y, D3DRS_ADAPTIVETESS_Z,
      D3DRS_ADAPTIVETESS_W, D3DRS_ENABLEADAPTIVETESSELLATION, D3DRS_TWOSIDEDSTENCILMODE, D3DRS_CCW_STENCILFAIL,
      D3DRS_CCW_STENCILZFAIL, D3DRS_CCW_STENCILPASS, D3DRS_CCW_STENCILFUNC, D3DRS_COLORWRITEENABLE1,
      D3DRS_COLORWRITEENABLE2, D3DRS_COLORWRITEENABLE3, D3DRS_BLENDFACTOR, D3DRS_SRGBWRITEENABLE,
      D3DRS_DEPTHBIAS, D3DRS_SEPARATEALPHABLENDENABLE, D3DRS_SRCBLENDALPHA, D3DRS_DESTBLENDALPHA, D3DRS_BLENDOPALPHA,
    };
    for (const auto state : kStates) {
      require(size_t(state) < frame.renderStates.size(), "Replay render state is absent.");
      check(pDevice->SetRenderState(state, frame.renderStates[state]), "SetRenderState captured value");
    }
    for (uint32_t slot = 0; slot < frame.samplers.size(); slot++) {
      for (uint32_t state = 1; state < frame.samplers[slot].size(); state++) {
        check(pDevice->SetSamplerState(slot, D3DSAMPLERSTATETYPE(state), frame.samplers[slot][state]), "SetSamplerState captured value");
      }
    }
    check(pDevice->SetViewport(&frame.viewport), "SetViewport captured value");
    check(pDevice->SetScissorRect(&frame.scissor), "SetScissorRect captured value");
    constexpr D3DTRANSFORMSTATETYPE kTransforms[] = { D3DTS_WORLD, D3DTS_VIEW, D3DTS_PROJECTION };
    for (uint32_t i = 0; i < 3; i++) {
      check(pDevice->SetTransform(kTransforms[i], &frame.transforms[i]), "SetTransform captured identity");
    }
    for (uint32_t stage = 0; stage < 2; stage++) {
      const auto& constants = frame.constants[stage];
      const auto floatCount = UINT(constants[0].size() / 16);
      const auto intCount = UINT(constants[1].size() / 16);
      require(constants[2].size() == 4, "Expected packed shader boolean constants.");
      uint32_t booleanMask;
      std::memcpy(&booleanMask, constants[2].data(), sizeof(booleanMask));
      BOOL booleans[16] {};
      for (uint32_t i = 0; i < 16; i++) {
        booleans[i] = BOOL((booleanMask >> i) & 1);
      }
      if (!stage) {
        check(pDevice->SetVertexShaderConstantF(0, reinterpret_cast<const float*>(constants[0].data()), floatCount), "SetVertexShaderConstantF captured");
        check(pDevice->SetVertexShaderConstantI(0, reinterpret_cast<const int*>(constants[1].data()), intCount), "SetVertexShaderConstantI captured");
        check(pDevice->SetVertexShaderConstantB(0, booleans, 16), "SetVertexShaderConstantB captured");
      } else {
        check(pDevice->SetPixelShaderConstantF(0, reinterpret_cast<const float*>(constants[0].data()), floatCount), "SetPixelShaderConstantF captured");
        check(pDevice->SetPixelShaderConstantI(0, reinterpret_cast<const int*>(constants[1].data()), intCount), "SetPixelShaderConstantI captured");
        check(pDevice->SetPixelShaderConstantB(0, booleans, 16), "SetPixelShaderConstantB captured");
      }
    }
  }

  void saveBitmap(IDirect3DDevice9* pDevice, IDirect3DSurface9* pTarget, const std::filesystem::path& path) {
    D3DSURFACE_DESC description {};
    check(pTarget->GetDesc(&description), "GetDesc raster bitmap");
    ComPointer<IDirect3DSurface9> readback;
    check(pDevice->CreateOffscreenPlainSurface(description.Width, description.Height, description.Format,
      D3DPOOL_SYSTEMMEM, readback.put(), nullptr), "CreateOffscreenPlainSurface raster bitmap");
    check(pDevice->GetRenderTargetData(pTarget, readback.get()), "GetRenderTargetData original MRT raster");
    D3DLOCKED_RECT mapping {};
    check(readback->LockRect(&mapping, nullptr, D3DLOCK_READONLY), "LockRect original MRT raster");
    BITMAPFILEHEADER fileHeader {};
    BITMAPINFOHEADER infoHeader {};
    fileHeader.bfType = 0x4d42;
    fileHeader.bfOffBits = sizeof(fileHeader) + sizeof(infoHeader);
    fileHeader.bfSize = fileHeader.bfOffBits + description.Width * description.Height * 4;
    infoHeader.biSize = sizeof(infoHeader);
    infoHeader.biWidth = LONG(description.Width);
    infoHeader.biHeight = -LONG(description.Height);
    infoHeader.biPlanes = 1;
    infoHeader.biBitCount = 32;
    infoHeader.biCompression = BI_RGB;
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(&fileHeader), sizeof(fileHeader));
    output.write(reinterpret_cast<const char*>(&infoHeader), sizeof(infoHeader));
    for (uint32_t row = 0; row < description.Height; row++) {
      output.write(static_cast<const char*>(mapping.pBits) + size_t(row) * mapping.Pitch, description.Width * 4);
    }
    check(readback->UnlockRect(), "UnlockRect raster bitmap");
    require(bool(output), "Could not save original raster bitmap.");
  }

  int replay(uint32_t frameCount, bool control, int skipTargetFrame,
      int mutateTargetFrame, int duplicateTargetFrame, bool presentationPattern, uint32_t sourceCamera,
      int resetBeforeFrame, bool nonidentityGamma, bool partialPresent) {
    const auto directory = std::filesystem::current_path();
    const auto fixture = readFixture(directory / "replay_fixture.bin");
    require(std::filesystem::is_regular_file(directory / "d3d9.dll"), "Replay requires the app-local test Runtime bundle.");
    const auto module = LoadLibraryExW((directory / "d3d9.dll").c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    require(module != nullptr, "Cannot load the app-local d3d9.dll.");
    using CreateD3d9 = IDirect3D9* (WINAPI*)(UINT);
    CreateD3d9 create;
    const auto address = GetProcAddress(module, "Direct3DCreate9");
    require(address != nullptr, "Direct3DCreate9 export is absent.");
    std::memcpy(&create, &address, sizeof(create));
    ReplayWindow window;
    ComPointer<IDirect3D9> api;
    *api.put() = create(D3D_SDK_VERSION);
    require(api.get() != nullptr, "Direct3DCreate9 returned null.");
    D3DPRESENT_PARAMETERS present {};
    present.BackBufferWidth = 1920;
    present.BackBufferHeight = 1080;
    present.BackBufferFormat = D3DFMT_A8R8G8B8;
    present.BackBufferCount = 1;
    present.SwapEffect = partialPresent ? D3DSWAPEFFECT_COPY : D3DSWAPEFFECT_DISCARD;
    present.hDeviceWindow = window.get();
    present.Windowed = TRUE;
    present.EnableAutoDepthStencil = TRUE;
    present.AutoDepthStencilFormat = D3DFMT_D24S8;
    present.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    ComPointer<IDirect3DDevice9> device;
    check(api->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, window.get(),
      D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &present, device.put()), "CreateDevice GPU replay");
    ComPointer<IDirect3DVertexShader9> vertexShader;
    ComPointer<IDirect3DPixelShader9> pixelShader;
    check(device->CreateVertexShader(reinterpret_cast<const DWORD*>(fixture.vertexShader.data()), vertexShader.put()), "CreateVertexShader original SM3");
    check(device->CreatePixelShader(reinterpret_cast<const DWORD*>(fixture.pixelShader.data()), pixelShader.put()), "CreatePixelShader original SM3");
    constexpr D3DVERTEXELEMENT9 kElements[] = {
      { 0, 0, D3DDECLTYPE_UBYTE4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_BINORMAL, 0 },
      { 1, 0, D3DDECLTYPE_UBYTE4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_NORMAL, 0 },
      { 2, 0, D3DDECLTYPE_SHORT4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0 },
      { 3, 0, D3DDECLTYPE_UBYTE4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TANGENT, 0 },
      { 4, 0, D3DDECLTYPE_SHORT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0 },
      D3DDECL_END()
    };
    ComPointer<IDirect3DVertexDeclaration9> declaration;
    check(device->CreateVertexDeclaration(kElements, declaration.put()), "CreateVertexDeclaration original layout");
    constexpr uint32_t kStrides[] = { 4, 4, 8, 4, 4 };
    std::array<ComPointer<IDirect3DVertexBuffer9>, 5> streams;
    ComPointer<IDirect3DVertexBuffer9> mutatedPosition;
    ComPointer<IDirect3DIndexBuffer9> indices;
    auto createBuffers = [&] {
      for (uint32_t i = 0; i < streams.size(); i++) {
        require(fixture.streams[i].size() == 252 * kStrides[i], "Replay vertex byte range does not match validated mesh.");
        check(device->CreateVertexBuffer(882 * kStrides[i], D3DUSAGE_WRITEONLY, 0, D3DPOOL_DEFAULT,
          streams[i].put(), nullptr), "CreateVertexBuffer captured stream");
        writeVertexBuffer(streams[i].get(), fixture.streams[i], kStrides[i]);
      }
      check(device->CreateVertexBuffer(882 * 8, D3DUSAGE_WRITEONLY, 0, D3DPOOL_DEFAULT,
        mutatedPosition.put(), nullptr), "CreateVertexBuffer negative control");
      auto mutatedBytes = fixture.streams[2];
      mutatedBytes[0] ^= 1;
      writeVertexBuffer(mutatedPosition.get(), mutatedBytes, 8);
      check(device->CreateIndexBuffer(UINT(fixture.indices.size()), D3DUSAGE_WRITEONLY, D3DFMT_INDEX16,
        D3DPOOL_DEFAULT, indices.put(), nullptr), "CreateIndexBuffer captured indices");
      void* pIndexMapping = nullptr;
      check(indices->Lock(0, 0, &pIndexMapping, 0), "Lock captured index buffer");
      std::memcpy(pIndexMapping, fixture.indices.data(), fixture.indices.size());
      check(indices->Unlock(), "Unlock captured index buffer");
    };
    createBuffers();
    ComPointer<IDirect3DTexture9> texture;
    check(device->CreateTexture(128, 128, 8, 0, D3DFMT_DXT5, D3DPOOL_MANAGED, texture.put(), nullptr), "CreateTexture synthetic DXT5 checker");
    fillTexture(texture.get());
    std::array<ComPointer<IDirect3DCubeTexture9>, 2> cubes;
    check(device->CreateCubeTexture(64, 7, 0, D3DFMT_DXT1, D3DPOOL_MANAGED, cubes[0].put(), nullptr), "CreateCubeTexture synthetic environment0");
    check(device->CreateCubeTexture(256, 9, 0, D3DFMT_DXT1, D3DPOOL_MANAGED, cubes[1].put(), nullptr), "CreateCubeTexture synthetic environment1");
    for (auto& cube : cubes) {
      fillCube(cube.get());
    }
    std::array<ComPointer<IDirect3DTexture9>, 3> targetTextures;
    std::array<ComPointer<IDirect3DSurface9>, 3> targets;
    ComPointer<IDirect3DSurface9> backBuffer;
    ComPointer<IDirect3DSurface9> depth;
    auto createTargets = [&] {
      for (uint32_t i = 0; i < targets.size(); i++) {
        check(device->CreateTexture(1920, 1080, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8,
          D3DPOOL_DEFAULT, targetTextures[i].put(), nullptr), "CreateTexture offscreen MRT");
        check(targetTextures[i]->GetSurfaceLevel(0, targets[i].put()), "GetSurfaceLevel offscreen MRT");
      }
      check(device->GetRenderTarget(0, backBuffer.put()), "GetRenderTarget original backbuffer");
      check(device->GetDepthStencilSurface(depth.put()), "GetDepthStencilSurface original D24S8");
    };
    createTargets();
    auto bindResources = [&] {
      check(device->SetVertexShader(vertexShader.get()), "SetVertexShader original SM3");
      check(device->SetPixelShader(pixelShader.get()), "SetPixelShader original SM3");
      check(device->SetVertexDeclaration(declaration.get()), "SetVertexDeclaration original layout");
      check(device->SetIndices(indices.get()), "SetIndices captured indices");
      for (uint32_t i = 0; i < streams.size(); i++) {
        check(device->SetStreamSource(i, streams[i].get(), 0, kStrides[i]), "SetStreamSource original stream");
        check(device->SetStreamSourceFreq(i, 1), "SetStreamSourceFreq non-instanced");
      }
      check(device->SetTexture(0, texture.get()), "SetTexture synthetic base");
      check(device->SetTexture(1, cubes[0].get()), "SetTexture synthetic environment0");
      check(device->SetTexture(2, cubes[1].get()), "SetTexture synthetic environment1");
    };
    bindResources();
    if (nonidentityGamma) {
      D3DGAMMARAMP ramp {};
      for (uint32_t i = 0; i < 256; i++) {
        const WORD value = WORD((i * i * 65535u) / (255u * 255u));
        ramp.red[i] = ramp.green[i] = ramp.blue[i] = value;
      }
      device->SetGammaRamp(0, 0, &ramp);
    }
    std::cout << "{\"event\":\"startup\",\"architecture\":\"" << (sizeof(void*) == 8 ? "x64" : "x86")
              << "\",\"frames\":" << frameCount << ",\"syntheticTextures\":true,\"originalShaderBytecode\":true,\"negativeControl\":"
              << (control ? "true" : "false");
    if (skipTargetFrame >= 0) {
      std::cout << ",\"skipTargetFrame\":" << skipTargetFrame
                << ",\"forcedNegativeControlOnSkippedFrame\":true";
    }
    std::cout << "}\n";
    for (uint32_t frameNumber = 0; frameNumber < frameCount; frameNumber++) {
      window.pump();
      if (int(frameNumber) == resetBeforeFrame) {
        for (uint32_t i = 0; i < streams.size(); i++) {
          check(device->SetStreamSource(i, nullptr, 0, 0), "Unbind default-pool stream before Reset");
          streams[i].reset();
        }
        check(device->SetIndices(nullptr), "Unbind default-pool indices before Reset");
        check(device->SetRenderTarget(1, nullptr), "Unbind MRT1 before Reset");
        check(device->SetRenderTarget(2, nullptr), "Unbind MRT2 before Reset");
        check(device->SetDepthStencilSurface(nullptr), "Unbind depth before Reset");
        mutatedPosition.reset();
        indices.reset();
        for (uint32_t i = 0; i < targets.size(); i++) {
          targets[i].reset();
          targetTextures[i].reset();
        }
        backBuffer.reset();
        depth.reset();
        check(device->Reset(&present), "Reset explicit lifecycle test");
        createBuffers();
        createTargets();
        bindResources();
        std::cout << "{\"event\":\"reset_completed\",\"replayFrame\":" << frameNumber
                  << ",\"recreatedDefaultPoolResources\":true}\n";
      }
      const auto& frame = fixture.frames[(frameNumber + sourceCamera) % 3];
      for (uint32_t i = 0; i < targets.size(); i++) {
        check(device->SetRenderTarget(i, targets[i].get()), "SetRenderTarget original offscreen MRT");
      }
      check(device->SetRenderTarget(3, nullptr), "SetRenderTarget3 absent");
      check(device->SetDepthStencilSurface(depth.get()), "SetDepthStencilSurface captured format");
      setFrameState(device.get(), frame);
      check(device->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, 0xff101010, 1.f, 0), "Clear captured pass");
      check(device->BeginScene(), "BeginScene");
      const bool targetSkipped = int(frameNumber) == skipTargetFrame;
      const bool targetMutated = int(frameNumber) == mutateTargetFrame;
      const bool targetDuplicated = int(frameNumber) == duplicateTargetFrame;
      if (control || targetSkipped) {
        check(device->SetStreamSource(2, mutatedPosition.get(), 0, 8), "SetStreamSource mutated POSITION negative control");
        check(device->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 882, 0, 144), "DrawIndexedPrimitive unknown mesh must preserve raster");
        check(device->SetStreamSource(2, streams[2].get(), 0, 8), "Restore original POSITION stream");
      }
      if (!targetSkipped) {
        if (targetMutated) {
          check(device->SetStreamSource(2, mutatedPosition.get(), 0, 8), "SetStreamSource mutated target POSITION");
        }
        check(device->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 882, 0, 144), "DrawIndexedPrimitive validated mesh");
        if (targetDuplicated) {
          check(device->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 882, 0, 144), "DrawIndexedPrimitive duplicate target must reject preview");
        }
        check(device->SetStreamSource(2, streams[2].get(), 0, 8), "Restore target POSITION after negative frame");
      }
      check(device->EndScene(), "EndScene");
      if (frameNumber < 3) {
        saveBitmap(device.get(), targets[0].get(), directory / ("original_mrt0_" + std::to_string(frame.originalFrame) + ".bmp"));
      }
      check(device->SetRenderTarget(1, nullptr), "Unbind MRT1");
      check(device->SetRenderTarget(2, nullptr), "Unbind MRT2");
      check(device->SetRenderTarget(0, backBuffer.get()), "Restore original backbuffer");
      check(device->StretchRect(targets[0].get(), nullptr, backBuffer.get(), nullptr, D3DTEXF_NONE), "StretchRect original MRT0 to backbuffer");
      if (presentationPattern) {
        for (uint32_t i = 0; i < 8; i++) {
          const RECT rectangle { LONG(i * 240), 32, LONG((i + 1) * 240), 96 };
          const uint32_t color = 0xff000000 | (((31 + i * 29 + frameNumber * 7) & 255) << 16)
            | (((17 + i * 53) & 255) << 8) | ((11 + i * 73) & 255);
          check(device->ColorFill(backBuffer.get(), &rectangle, color), "ColorFill WSI preservation pattern");
        }
      }
      const RECT sourceRect { 0, 0, 1920, 1080 };
      const RECT destinationRect { 100, 100, 1820, 980 };
      check(device->Present(partialPresent ? &sourceRect : nullptr,
        partialPresent ? &destinationRect : nullptr, nullptr, nullptr), "Present");
      std::cout << "{\"event\":\"frame\",\"replayFrame\":" << frameNumber << ",\"sourceFrame\":" << frame.originalFrame
                << ",\"draws\":" << (targetSkipped ? 1 : (control ? 2 : 1) + unsigned(targetDuplicated))
                << ",\"hresultChecks\":" << s_checkedCalls;
      if (skipTargetFrame >= 0) {
        std::cout << ",\"targetSkipped\":" << (targetSkipped ? "true" : "false");
      }
      std::cout << ",\"targetMutated\":" << (targetMutated ? "true" : "false")
                << ",\"targetDuplicated\":" << (targetDuplicated ? "true" : "false")
                << ",\"presentationPattern\":" << (presentationPattern ? "true" : "false")
                << ",\"nonidentityGamma\":" << (nonidentityGamma ? "true" : "false")
                << ",\"partialPresent\":" << (partialPresent ? "true" : "false");
      std::cout << "}\n";
    }
    finishReplay(device.get(), window, directory);
    std::ofstream report(directory / "replay_report.json");
    report << "{\"result\":\"passed\",\"frames\":" << frameCount << ",\"hresultChecks\":" << s_checkedCalls
           << ",\"syntheticTextures\":true,\"RTXStages\":\"verify_runtime_logs_separately\"";
    if (skipTargetFrame >= 0) {
      report << ",\"skipTargetFrame\":" << skipTargetFrame;
    }
    report << "}\n";
    std::cout << "{\"event\":\"success\",\"hresultChecks\":" << s_checkedCalls << "}\n";
    return 0;
  }
}

int wmain(int argc, wchar_t** argv) {
  try {
    if (argc == 3 && std::wstring(argv[1]) == L"--audit-callback-log") {
      // CPU-only regression entry point: this branch never loads D3D9 and does
      // not create a device, window or GPU command stream.
      const auto result = auditCallbackCompletion(argv[2]);
      printCallbackCompletion(result, "callback_log_audit");
      return result.complete() ? 0 : 2;
    }
    uint32_t frames = 3;
    bool control = true;
    int skipTargetFrame = -1;
    int mutateTargetFrame = -1;
    int duplicateTargetFrame = -1;
    bool presentationPattern = false;
    uint32_t sourceCamera = 0;
    int resetBeforeFrame = -1;
    bool nonidentityGamma = false;
    bool partialPresent = false;
    for (int i = 1; i < argc; i++) {
      if (std::wstring(argv[i]) == L"--frames" && i + 1 < argc) {
        frames = uint32_t(std::stoul(argv[++i]));
      } else if (std::wstring(argv[i]) == L"--no-control") {
        control = false;
      } else if (std::wstring(argv[i]) == L"--skip-target-frame" && i + 1 < argc) {
        require(skipTargetFrame < 0, "Specify --skip-target-frame once.");
        const auto value = std::stoul(argv[++i]);
        require(value < 90, "Skipped target frame must be between 0 and 89.");
        skipTargetFrame = int(value);
      } else if (std::wstring(argv[i]) == L"--mutate-target-frame" && i + 1 < argc) {
        require(mutateTargetFrame < 0, "Specify --mutate-target-frame once.");
        const auto value = std::stoul(argv[++i]);
        require(value < 90, "Mutated target frame must be between 0 and 89.");
        mutateTargetFrame = int(value);
      } else if (std::wstring(argv[i]) == L"--duplicate-target-frame" && i + 1 < argc) {
        require(duplicateTargetFrame < 0, "Specify --duplicate-target-frame once.");
        const auto value = std::stoul(argv[++i]);
        require(value < 90, "Duplicated target frame must be between 0 and 89.");
        duplicateTargetFrame = int(value);
      } else if (std::wstring(argv[i]) == L"--presentation-pattern") {
        presentationPattern = true;
      } else if (std::wstring(argv[i]) == L"--source-camera" && i + 1 < argc) {
        sourceCamera = uint32_t(std::stoul(argv[++i]));
        require(sourceCamera < 3, "Source camera must be 0, 1 or 2.");
      } else if (std::wstring(argv[i]) == L"--reset-before-frame" && i + 1 < argc) {
        require(resetBeforeFrame < 0, "Specify --reset-before-frame once.");
        const auto value = std::stoul(argv[++i]);
        require(value < 90, "Reset frame must be between 0 and 89.");
        resetBeforeFrame = int(value);
      } else if (std::wstring(argv[i]) == L"--nonidentity-gamma") {
        nonidentityGamma = true;
      } else if (std::wstring(argv[i]) == L"--partial-present") {
        partialPresent = true;
      } else {
        throw std::runtime_error("Arguments: --frames 1..90 [--no-control] [--skip-target-frame 0..89] [--mutate-target-frame 0..89] [--duplicate-target-frame 0..89] [--presentation-pattern] [--source-camera 0..2] [--reset-before-frame 0..89] [--nonidentity-gamma] [--partial-present].");
      }
    }
    require(frames && frames <= 90, "Replay frames must be between 1 and 90.");
    require(skipTargetFrame < 0 || uint32_t(skipTargetFrame) < frames, "Skipped target frame must be inside the requested frame range.");
    require(mutateTargetFrame < 0 || uint32_t(mutateTargetFrame) < frames, "Mutated target frame must be inside the requested frame range.");
    require(duplicateTargetFrame < 0 || uint32_t(duplicateTargetFrame) < frames, "Duplicated target frame must be inside the requested frame range.");
    require(mutateTargetFrame < 0 || mutateTargetFrame != skipTargetFrame, "Mutation and absence tests require distinct frames.");
    require(duplicateTargetFrame < 0 || (duplicateTargetFrame != skipTargetFrame && duplicateTargetFrame != mutateTargetFrame), "Duplicate and absence/mutation tests require distinct frames.");
    require(resetBeforeFrame < 0 || uint32_t(resetBeforeFrame) < frames, "Reset frame must be inside the requested frame range.");
    require(resetBeforeFrame < 0 || (!nonidentityGamma && !partialPresent), "Reset is tested separately from gamma and partial presentation.");
    return replay(frames, control, skipTargetFrame, mutateTargetFrame, duplicateTargetFrame, presentationPattern,
      sourceCamera, resetBeforeFrame, nonidentityGamma, partialPresent);
  } catch (const std::exception& error) {
    std::cerr << "Replay failed: " << error.what() << '\n';
    return 1;
  }
}
