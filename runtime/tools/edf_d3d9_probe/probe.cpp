/*
 * Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
 *
 * SPDX-License-Identifier: MIT
 */

#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <windows.h>
#include <d3d9.h>
#include <d3dcompiler.h>

#include "../../src/util/sha1/sha1_util.h"

namespace {
  constexpr uint32_t kWidth = 640;
  constexpr uint32_t kHeight = 480;
  constexpr uint32_t kDrawsPerFrame = 8;

  std::string escapeJson(const std::string& value) {
    std::ostringstream result;
    for (unsigned char character : value) {
      if (character == '"' || character == '\\') {
        result << '\\' << char(character);
      } else if (character < 0x20) {
        result << "\\u" << std::hex << std::setw(4) << std::setfill('0') << uint32_t(character);
      } else {
        result << char(character);
      }
    }
    return result.str();
  }

  void checkResult(HRESULT result, const char* pOperation) {
    if (FAILED(result)) {
      std::ostringstream message;
      message << pOperation << " failed: HRESULT 0x" << std::hex << uint32_t(result);
      throw std::runtime_error(message.str());
    }
  }

  template<typename T>
  class ComPointer {
  public:
    ComPointer() = default;
    ~ComPointer() {
      if (m_pObject != nullptr) {
        m_pObject->Release();
      }
    }
    ComPointer(const ComPointer&) = delete;
    ComPointer& operator=(const ComPointer&) = delete;
    T* get() const { return m_pObject; }
    T* operator->() const { return m_pObject; }
    T** put() { return &m_pObject; }

  private:
    T* m_pObject = nullptr;
  };

  class Library {
  public:
    Library(const std::filesystem::path& path, DWORD flags)
      : m_module(LoadLibraryExW(path.c_str(), nullptr, flags)) {
      if (m_module == nullptr) {
        throw std::runtime_error("Cannot load " + path.u8string()
          + ": Win32 error " + std::to_string(GetLastError()));
      }
    }
    ~Library() { FreeLibrary(m_module); }
    Library(const Library&) = delete;
    Library& operator=(const Library&) = delete;

    template<typename T>
    T loadExport(const char* pName) const {
      const auto address = GetProcAddress(m_module, pName);
      if (address == nullptr) {
        throw std::runtime_error(std::string("Missing export ") + pName);
      }
      T function;
      static_assert(sizeof(function) == sizeof(address), "Function pointer size mismatch");
      std::memcpy(&function, &address, sizeof(function));
      return function;
    }

    std::filesystem::path path() const {
      std::vector<wchar_t> buffer(32768);
      const auto length = GetModuleFileNameW(m_module, buffer.data(), DWORD(buffer.size()));
      if (!length || length == buffer.size()) {
        throw std::runtime_error("Cannot obtain loaded module path");
      }
      return std::filesystem::path(std::wstring(buffer.data(), length));
    }

  private:
    HMODULE m_module;
  };

  LRESULT CALLBACK processWindow(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    if (message == WM_DESTROY) {
      PostQuitMessage(0);
      return 0;
    }
    return DefWindowProcW(window, message, wParam, lParam);
  }

  class ProbeWindow {
  public:
    ProbeWindow() {
      WNDCLASSW windowClass = {};
      windowClass.lpfnWndProc = processWindow;
      windowClass.hInstance = GetModuleHandleW(nullptr);
      windowClass.lpszClassName = L"EdfD3d9DiagnosticProbe";
      if (!RegisterClassW(&windowClass)) {
        throw std::runtime_error("RegisterClassW failed");
      }
      m_window = CreateWindowW(windowClass.lpszClassName, L"D3D9 compatibility capture probe",
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, kWidth, kHeight,
        nullptr, nullptr, windowClass.hInstance, nullptr);
      if (m_window == nullptr) {
        throw std::runtime_error("CreateWindowW failed");
      }
      ShowWindow(m_window, SW_SHOWNOACTIVATE);
    }
    ~ProbeWindow() {
      if (m_window != nullptr && IsWindow(m_window)) {
        DestroyWindow(m_window);
      }
    }
    HWND get() const { return m_window; }
    void pumpMessages() const {
      MSG message;
      while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        if (message.message == WM_QUIT) {
          throw std::runtime_error("Probe window closed before all frames completed");
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
      }
    }

  private:
    HWND m_window = nullptr;
  };

  struct Vertex {
    float x, y, z;
    float nx, ny, nz;
    float u, v;
  };
  static_assert(sizeof(Vertex) == 32, "Unexpected vertex stride");

  constexpr std::array<Vertex, 8> kVertices = {{
    { 9.0f, 9.0f, 0.5f, 0, 0, 1, 0, 0 },
    { 8.0f, 8.0f, 0.5f, 0, 0, 1, 0, 0 },
    { -0.7f, -0.5f, 0.5f, 0, 0, 1, 0, 1 },
    { 0.0f, 0.6f, 0.5f, 0, 0, 1, 0.5f, 0 },
    { 0.7f, -0.5f, 0.5f, 0, 0, 1, 1, 1 },
    { -0.2f, -0.2f, 0.4f, 0, 0, 1, 0, 1 },
    { 0.0f, 0.2f, 0.4f, 0, 0, 1, 0.5f, 0 },
    { 0.2f, -0.2f, 0.4f, 0, 0, 1, 1, 1 }
  }};
  constexpr std::array<uint16_t, 9> kIndices = {{ 7, 7, 7, 0, 1, 2, 0, 0, 0 }};
  constexpr std::array<uint16_t, 3> kUpIndices = {{ 1, 2, 3 }};
  constexpr std::array<Vertex, 4> kUpVertices = {{
    kVertices[0], kVertices[2], kVertices[3], kVertices[4]
  }};

  constexpr char kVertexShader[] = R"(
float4 WvpRows[4] : register(c0);
struct Input { float3 position : POSITION0; float3 normal : NORMAL0; float2 uv : TEXCOORD0; };
struct Output { float4 position : POSITION0; float2 uv : TEXCOORD0; float3 normal : TEXCOORD1; };
Output main(Input input) {
  Output output;
  float4 position = float4(input.position, 1.0);
  output.position = float4(dot(position, WvpRows[0]), dot(position, WvpRows[1]),
    dot(position, WvpRows[2]), dot(position, WvpRows[3]));
  output.uv = input.uv;
  output.normal = input.normal;
  return output;
})";

  constexpr char kPixelShader[] = R"(
sampler2D BaseTexture : register(s0);
float4 Tint : register(c0);
float4 LightDirection : register(c1);
float4 main(float2 uv : TEXCOORD0, float3 normal : TEXCOORD1) : COLOR0 {
  return tex2D(BaseTexture, uv) * Tint * saturate(dot(normal, LightDirection.xyz));
})";

  std::vector<uint8_t> compileShader(const Library& compiler, const char* pSource,
    const char* pStage, const char* pTarget) {
    const auto compile = compiler.loadExport<pD3DCompile>("D3DCompile");
    ComPointer<ID3DBlob> code;
    ComPointer<ID3DBlob> errors;
    const auto result = compile(pSource, std::strlen(pSource), pStage, nullptr, nullptr,
      "main", pTarget, D3DCOMPILE_SKIP_OPTIMIZATION,
      0, code.put(), errors.put());
    if (errors.get() != nullptr) {
      std::cerr.write(static_cast<const char*>(errors->GetBufferPointer()),
        std::streamsize(errors->GetBufferSize()));
    }
    checkResult(result, "D3DCompile");
    std::vector<uint8_t> bytes(code->GetBufferSize());
    std::memcpy(bytes.data(), code->GetBufferPointer(), bytes.size());
    const auto hash = dxvk::Sha1Hash::compute(bytes.data(), bytes.size()).toString();
    const auto directory = std::filesystem::current_path() / "probe_shaders";
    std::filesystem::create_directories(directory);
    const auto path = directory / (std::string(pStage) + "_" + hash + ".dxso");
    std::ofstream binary(path, std::ios::binary);
    binary.write(reinterpret_cast<const char*>(bytes.data()), std::streamsize(bytes.size()));
    binary.close();
    if (!binary) {
      throw std::runtime_error("Cannot write probe shader bytecode");
    }
    const auto disassemble = compiler.loadExport<decltype(&D3DDisassemble)>("D3DDisassemble");
    ComPointer<ID3DBlob> assembly;
    checkResult(disassemble(bytes.data(), bytes.size(), 0, nullptr, assembly.put()), "D3DDisassemble");
    std::ofstream text(path.wstring() + L".dis", std::ios::binary);
    text.write(static_cast<const char*>(assembly->GetBufferPointer()),
      std::streamsize(assembly->GetBufferSize()));
    text.close();
    if (!text) {
      throw std::runtime_error("Cannot write probe shader disassembly");
    }
    std::cout << "{\"probe_event\":\"shader\",\"stage\":\"" << pStage
      << "\",\"sha1\":\"" << hash << "\",\"bytes\":" << bytes.size() << "}\n";
    return bytes;
  }

  template<typename T>
  void verifyShader(T* pShader, const std::vector<uint8_t>& expected) {
    UINT size = 0;
    checkResult(pShader->GetFunction(nullptr, &size), "GetFunction size");
    std::vector<uint8_t> bytes(size);
    checkResult(pShader->GetFunction(bytes.data(), &size), "GetFunction bytes");
    if (bytes != expected) {
      throw std::runtime_error("GetFunction differs from compiled SM3 bytecode");
    }
  }

  void writeBuffers(IDirect3DVertexBuffer9* pVertices, IDirect3DIndexBuffer9* pIndices) {
    void* pMapping = nullptr;
    checkResult(pVertices->Lock(0, UINT(sizeof(kVertices)), &pMapping, D3DLOCK_DISCARD), "VB DISCARD");
    std::memcpy(pMapping, kVertices.data(), sizeof(kVertices));
    checkResult(pVertices->Unlock(), "VB Unlock");
    checkResult(pIndices->Lock(0, UINT(sizeof(kIndices)), &pMapping, D3DLOCK_DISCARD), "IB DISCARD");
    std::memcpy(pMapping, kIndices.data(), sizeof(kIndices));
    checkResult(pIndices->Unlock(), "IB Unlock");
  }

  void updateBufferTails(IDirect3DVertexBuffer9* pVertices, IDirect3DIndexBuffer9* pIndices,
    uint32_t frame) {
    std::array<Vertex, 3> tail = {{ kVertices[5], kVertices[6], kVertices[7] }};
    for (auto& vertex : tail) {
      vertex.x += float(frame + 1) * 0.01f;
    }
    void* pMapping = nullptr;
    checkResult(pVertices->Lock(5 * sizeof(Vertex), UINT(sizeof(tail)), &pMapping,
      D3DLOCK_NOOVERWRITE), "VB NOOVERWRITE");
    std::memcpy(pMapping, tail.data(), sizeof(tail));
    checkResult(pVertices->Unlock(), "VB Unlock");
    checkResult(pIndices->Lock(6 * sizeof(uint16_t), 3 * sizeof(uint16_t), &pMapping,
      D3DLOCK_NOOVERWRITE), "IB NOOVERWRITE");
    constexpr uint16_t kReversed[] = { 2, 1, 0 };
    std::memcpy(pMapping, kReversed, sizeof(kReversed));
    checkResult(pIndices->Unlock(), "IB Unlock");
  }

  void verifyUpReset(IDirect3DDevice9* pDevice, bool checkIndices) {
    ComPointer<IDirect3DVertexBuffer9> stream;
    UINT offset = 0;
    UINT stride = 0;
    checkResult(pDevice->GetStreamSource(0, stream.put(), &offset, &stride), "GetStreamSource after UP");
    if (stream.get() != nullptr) {
      throw std::runtime_error("UP draw did not clear stream 0");
    }
    if (checkIndices) {
      ComPointer<IDirect3DIndexBuffer9> indices;
      checkResult(pDevice->GetIndices(indices.put()), "GetIndices after UP");
      if (indices.get() != nullptr) {
        throw std::runtime_error("Indexed UP draw did not clear the index buffer");
      }
    }
  }

  void logDraw(uint32_t frame, uint32_t draw, const char* pApi, const char* pPhase) {
    std::cout << "{\"probe_event\":\"draw\",\"frame\":" << frame << ",\"draw\":" << draw
      << ",\"api\":\"" << pApi << "\",\"phase\":\"" << pPhase << "\"}\n";
  }

  int runProbe(uint32_t frameCount) {
    ProbeWindow window;
    std::vector<wchar_t> executablePath(32768);
    const auto pathLength = GetModuleFileNameW(nullptr, executablePath.data(), DWORD(executablePath.size()));
    if (!pathLength || pathLength == executablePath.size()) {
      throw std::runtime_error("Cannot obtain probe executable path");
    }
    const auto appLocal = std::filesystem::path(std::wstring(executablePath.data(), pathLength))
      .parent_path() / "d3d9.dll";
    const bool localRuntime = std::filesystem::is_regular_file(appLocal);
    Library compiler(L"d3dcompiler_47.dll", LOAD_LIBRARY_SEARCH_SYSTEM32);
    Library d3d(localRuntime ? appLocal : std::filesystem::path(L"d3d9.dll"),
      localRuntime ? LOAD_WITH_ALTERED_SEARCH_PATH : LOAD_LIBRARY_SEARCH_SYSTEM32);
    std::cout << "{\"probe_event\":\"startup\",\"architecture\":\""
      << (sizeof(void*) == 8 ? "x64" : "x86") << "\",\"frames\":" << frameCount
      << ",\"draws_per_frame\":" << kDrawsPerFrame << ",\"app_local_runtime\":"
      << (localRuntime ? "true" : "false") << ",\"d3d9\":\""
      << escapeJson(d3d.path().u8string()) << "\"}\n";
    using CreateD3d9 = IDirect3D9* (WINAPI*)(UINT);
    const auto create = d3d.loadExport<CreateD3d9>("Direct3DCreate9");
    ComPointer<IDirect3D9> api;
    *api.put() = create(D3D_SDK_VERSION);
    if (api.get() == nullptr) {
      throw std::runtime_error("Direct3DCreate9 returned null");
    }
    D3DCAPS9 caps = {};
    checkResult(api->GetDeviceCaps(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, &caps), "GetDeviceCaps");
    if (caps.VertexShaderVersion < D3DVS_VERSION(3, 0) || caps.PixelShaderVersion < D3DPS_VERSION(3, 0)) {
      throw std::runtime_error("The selected D3D9 device does not support SM3");
    }
    D3DDISPLAYMODE mode = {};
    checkResult(api->GetAdapterDisplayMode(D3DADAPTER_DEFAULT, &mode), "GetAdapterDisplayMode");
    D3DPRESENT_PARAMETERS present = {};
    present.BackBufferWidth = kWidth;
    present.BackBufferHeight = kHeight;
    present.BackBufferFormat = mode.Format;
    present.BackBufferCount = 1;
    present.SwapEffect = D3DSWAPEFFECT_DISCARD;
    present.hDeviceWindow = window.get();
    present.Windowed = TRUE;
    present.EnableAutoDepthStencil = TRUE;
    present.AutoDepthStencilFormat = D3DFMT_D24S8;
    present.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    ComPointer<IDirect3DDevice9> device;
    checkResult(api->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, window.get(),
      D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE,
      &present, device.put()), "CreateDevice");

    const auto vsCode = compileShader(compiler, kVertexShader, "vs", "vs_3_0");
    const auto psCode = compileShader(compiler, kPixelShader, "ps", "ps_3_0");
    ComPointer<IDirect3DVertexShader9> vs;
    ComPointer<IDirect3DPixelShader9> ps;
    checkResult(device->CreateVertexShader(reinterpret_cast<const DWORD*>(vsCode.data()), vs.put()),
      "CreateVertexShader");
    checkResult(device->CreatePixelShader(reinterpret_cast<const DWORD*>(psCode.data()), ps.put()),
      "CreatePixelShader");
    verifyShader(vs.get(), vsCode);
    verifyShader(ps.get(), psCode);
    constexpr D3DVERTEXELEMENT9 kDeclaration[] = {
      { 0, 0, D3DDECLTYPE_FLOAT3, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0 },
      { 0, 12, D3DDECLTYPE_FLOAT3, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_NORMAL, 0 },
      { 0, 24, D3DDECLTYPE_FLOAT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0 },
      D3DDECL_END()
    };
    ComPointer<IDirect3DVertexDeclaration9> declaration;
    checkResult(device->CreateVertexDeclaration(kDeclaration, declaration.put()), "CreateVertexDeclaration");
    ComPointer<IDirect3DVertexBuffer9> vertices;
    ComPointer<IDirect3DIndexBuffer9> indices;
    checkResult(device->CreateVertexBuffer(UINT(sizeof(kVertices)), D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY,
      0, D3DPOOL_DEFAULT, vertices.put(), nullptr), "CreateVertexBuffer");
    checkResult(device->CreateIndexBuffer(UINT(sizeof(kIndices)), D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY,
      D3DFMT_INDEX16, D3DPOOL_DEFAULT, indices.put(), nullptr), "CreateIndexBuffer");
    ComPointer<IDirect3DTexture9> texture;
    checkResult(device->CreateTexture(2, 2, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED,
      texture.put(), nullptr), "CreateTexture");
    D3DLOCKED_RECT mapping = {};
    checkResult(texture->LockRect(0, &mapping, nullptr, 0), "Texture LockRect");
    constexpr uint32_t kPixels[2][2] = {{ 0xffff0000, 0xff00ff00 }, { 0xff0000ff, 0xffffffff }};
    for (uint32_t row = 0; row < 2; ++row) {
      std::memcpy(static_cast<uint8_t*>(mapping.pBits) + row * mapping.Pitch, kPixels[row], sizeof(kPixels[row]));
    }
    checkResult(texture->UnlockRect(0), "Texture UnlockRect");
    ComPointer<IDirect3DSurface9> renderTarget;
    ComPointer<IDirect3DSurface9> depth;
    checkResult(device->GetRenderTarget(0, renderTarget.put()), "GetRenderTarget");
    checkResult(device->GetDepthStencilSurface(depth.put()), "GetDepthStencilSurface");
    checkResult(device->SetRenderTarget(0, renderTarget.get()), "SetRenderTarget");
    checkResult(device->SetDepthStencilSurface(depth.get()), "SetDepthStencilSurface");

    const D3DVIEWPORT9 viewport = { 0, 0, kWidth, kHeight, 0, 1 };
    const RECT scissor = { 0, 0, LONG(kWidth), LONG(kHeight) };
    checkResult(device->SetViewport(&viewport), "SetViewport");
    checkResult(device->SetScissorRect(&scissor), "SetScissorRect");
    checkResult(device->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE), "SetRenderState scissor");
    checkResult(device->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE), "SetRenderState cull");
    checkResult(device->SetRenderState(D3DRS_LIGHTING, FALSE), "SetRenderState lighting");
    checkResult(device->SetRenderState(D3DRS_ALPHABLENDENABLE, FALSE), "SetRenderState blend");
    checkResult(device->SetRenderState(D3DRS_ALPHATESTENABLE, FALSE), "SetRenderState alpha test");
    checkResult(device->SetRenderState(D3DRS_ZENABLE, TRUE), "SetRenderState depth");
    checkResult(device->SetRenderState(D3DRS_ZWRITEENABLE, FALSE), "SetRenderState depth write");
    checkResult(device->SetRenderState(D3DRS_ZFUNC, D3DCMP_LESSEQUAL), "SetRenderState depth compare");
    checkResult(device->SetRenderState(D3DRS_FOGENABLE, FALSE), "SetRenderState fog");
    checkResult(device->SetSamplerState(0, D3DSAMP_MINFILTER, D3DTEXF_POINT), "SetSamplerState min");
    checkResult(device->SetSamplerState(0, D3DSAMP_MAGFILTER, D3DTEXF_POINT), "SetSamplerState mag");
    checkResult(device->SetSamplerState(0, D3DSAMP_MIPFILTER, D3DTEXF_NONE), "SetSamplerState mip");
    checkResult(device->SetSamplerState(0, D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP), "SetSamplerState u");
    checkResult(device->SetSamplerState(0, D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP), "SetSamplerState v");
    checkResult(device->SetStreamSourceFreq(0, 1), "SetStreamSourceFreq");
    const D3DMATRIX identity = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 };
    D3DMATRIX scale = identity;
    scale._11 = 2.0f;
    D3DMATRIX translationCheck = identity;
    translationCheck._41 = 0.1f;
    checkResult(device->SetTransform(D3DTS_TEXTURE7, &scale), "Set transform order check");
    checkResult(device->MultiplyTransform(D3DTS_TEXTURE7, &translationCheck), "Multiply transform order check");
    D3DMATRIX observedTransform = {};
    checkResult(device->GetTransform(D3DTS_TEXTURE7, &observedTransform), "Get transform order check");
    D3DMATRIX expectedTransform = scale;
    expectedTransform._41 = 0.2f;
    if (std::memcmp(&observedTransform, &expectedTransform, sizeof(D3DMATRIX))) {
      throw std::runtime_error("MultiplyTransform did not pre-multiply translation by the existing scale");
    }
    checkResult(device->SetTransform(D3DTS_TEXTURE7, &identity), "Restore unused texture transform");
    std::cout << "{\"probe_event\":\"transform_order_check\",\"passed\":true}\n";
    ComPointer<IDirect3DStateBlock9> matrixBlock;
    checkResult(device->BeginStateBlock(), "Begin transform stateblock check");
    checkResult(device->SetTransform(D3DTS_TEXTURE7, &scale), "Record texture scale");
    checkResult(device->MultiplyTransform(D3DTS_TEXTURE7, &translationCheck), "Record texture multiply");
    checkResult(device->EndStateBlock(matrixBlock.put()), "End transform stateblock check");
    checkResult(device->GetTransform(D3DTS_TEXTURE7, &observedTransform), "Get transform before Apply");
    std::cout << "{\"probe_event\":\"transform_stateblock_before_apply\",\"scaleX\":"
      << observedTransform._11 << ",\"translationX\":" << observedTransform._41 << "}\n";
    if (std::memcmp(&observedTransform, &translationCheck, sizeof(D3DMATRIX))) {
      throw std::runtime_error("MultiplyTransform must affect live state while recording");
    }
    checkResult(matrixBlock->Apply(), "Apply transform stateblock check");
    checkResult(device->GetTransform(D3DTS_TEXTURE7, &observedTransform), "Get transform after Apply");
    std::cout << "{\"probe_event\":\"transform_stateblock_after_apply\",\"scaleX\":"
      << observedTransform._11 << ",\"translationX\":" << observedTransform._41 << "}\n";
    if (std::memcmp(&observedTransform, &scale, sizeof(D3DMATRIX))) {
      throw std::runtime_error("Stateblock must retain SetTransform without capturing MultiplyTransform");
    }
    checkResult(device->SetTransform(D3DTS_TEXTURE7, &identity), "Restore texture transform after Apply");
    std::cout << "{\"probe_event\":\"transform_stateblock_check\",\"passed\":true}\n";
    std::cout << "{\"probe_event\":\"layout\",\"stride\":32,\"position_offset\":0,"
      "\"normal_offset\":12,\"uv_offset\":24,\"vertex_count\":8,\"index_count\":9,\"index_bits\":16}\n";

    for (uint32_t frame = 0; frame < frameCount; ++frame) {
      window.pumpMessages();
      writeBuffers(vertices.get(), indices.get());
      checkResult(device->SetVertexShader(vs.get()), "SetVertexShader");
      checkResult(device->SetPixelShader(ps.get()), "SetPixelShader");
      checkResult(device->SetVertexDeclaration(declaration.get()), "SetVertexDeclaration");
      checkResult(device->SetTexture(0, texture.get()), "SetTexture");
      checkResult(device->SetIndices(indices.get()), "SetIndices");
      checkResult(device->SetStreamSource(0, vertices.get(), 0, sizeof(Vertex)), "SetStreamSource");
      const std::array<float, 16> wvp = {{
        0.8f, 0, 0, 0.05f + float(frame) * 0.02f,
        0, 0.8f, 0, -0.05f,
        0, 0, 1, 0,
        0, 0, 0, 1
      }};
      constexpr float kWhite[] = { 1, 1, 1, 1 };
      constexpr float kRed[] = { 1, 0.25f, 0.25f, 1 };
      constexpr float kLight[] = { 0, 0, 1, 0 };
      constexpr int kIntegers[] = { 1, 2, 3, 4 };
      constexpr BOOL kBoolean[] = { TRUE };
      checkResult(device->SetVertexShaderConstantF(0, wvp.data(), 4), "SetVertexShaderConstantF");
      checkResult(device->SetPixelShaderConstantF(0, kWhite, 1), "SetPixelShaderConstantF tint");
      checkResult(device->SetPixelShaderConstantF(1, kLight, 1), "SetPixelShaderConstantF light");
      checkResult(device->SetVertexShaderConstantI(0, kIntegers, 1), "SetVertexShaderConstantI");
      checkResult(device->SetPixelShaderConstantI(0, kIntegers, 1), "SetPixelShaderConstantI");
      checkResult(device->SetVertexShaderConstantB(0, kBoolean, 1), "SetVertexShaderConstantB");
      checkResult(device->SetPixelShaderConstantB(0, kBoolean, 1), "SetPixelShaderConstantB");
      checkResult(device->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL,
        D3DCOLOR_XRGB(16, 16, 16), 1, 0), "Clear");
      checkResult(device->BeginScene(), "BeginScene");

      logDraw(frame, 0, "DrawPrimitive", "programmable");
      checkResult(device->DrawPrimitive(D3DPT_TRIANGLELIST, 2, 1), "DrawPrimitive");
      checkResult(device->SetStreamSource(0, vertices.get(), sizeof(Vertex), sizeof(Vertex)), "SetStreamSource offset");
      logDraw(frame, 1, "DrawIndexedPrimitive", "programmable_offsets");
      checkResult(device->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 1, 0, 3, 3, 1), "DrawIndexedPrimitive");
      logDraw(frame, 2, "DrawPrimitiveUP", "programmable_up");
      checkResult(device->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, kVertices.data() + 2, sizeof(Vertex)),
        "DrawPrimitiveUP");
      verifyUpReset(device.get(), false);
      checkResult(device->SetStreamSource(0, vertices.get(), 0, sizeof(Vertex)), "Restore stream before indexed UP");
      checkResult(device->SetIndices(indices.get()), "Restore indices before indexed UP");
      logDraw(frame, 3, "DrawIndexedPrimitiveUP", "programmable_up_min_vertex");
      checkResult(device->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 1, 3, 1,
        kUpIndices.data(), D3DFMT_INDEX16, kUpVertices.data(), sizeof(Vertex)), "DrawIndexedPrimitiveUP");
      verifyUpReset(device.get(), true);
      checkResult(device->SetStreamSource(0, vertices.get(), 0, sizeof(Vertex)), "Restore stream after UP");
      checkResult(device->SetIndices(indices.get()), "Restore indices after UP");

      ComPointer<IDirect3DStateBlock9> stateBlock;
      checkResult(device->BeginStateBlock(), "BeginStateBlock");
      checkResult(device->SetVertexShader(vs.get()), "Record vertex shader");
      checkResult(device->SetPixelShader(ps.get()), "Record pixel shader");
      checkResult(device->SetVertexDeclaration(declaration.get()), "Record declaration");
      checkResult(device->SetStreamSource(0, vertices.get(), sizeof(Vertex), sizeof(Vertex)), "Record stream offset");
      checkResult(device->SetIndices(indices.get()), "Record indices");
      checkResult(device->SetTexture(0, texture.get()), "Record texture");
      checkResult(device->SetRenderState(D3DRS_ALPHABLENDENABLE, FALSE), "Record blend");
      checkResult(device->SetVertexShaderConstantF(0, wvp.data(), 4), "Record VS constants");
      checkResult(device->SetPixelShaderConstantF(0, kRed, 1), "Record PS constants");
      checkResult(device->EndStateBlock(stateBlock.put()), "EndStateBlock");
      float tint[4] = {};
      checkResult(device->GetPixelShaderConstantF(0, tint, 1), "Get constants before Apply");
      if (std::memcmp(tint, kWhite, sizeof(tint))) {
        throw std::runtime_error("Stateblock recording changed effective pixel constants");
      }
      logDraw(frame, 4, "DrawPrimitive", "recorded_state_not_applied");
      checkResult(device->DrawPrimitive(D3DPT_TRIANGLELIST, 2, 1), "Draw before Apply");
      updateBufferTails(vertices.get(), indices.get(), frame);
      checkResult(stateBlock->Apply(), "StateBlock Apply");
      checkResult(device->GetPixelShaderConstantF(0, tint, 1), "Get constants after Apply");
      if (std::memcmp(tint, kRed, sizeof(tint))) {
        throw std::runtime_error("Stateblock Apply did not restore pixel constants");
      }
      logDraw(frame, 5, "DrawIndexedPrimitive", "stateblock_applied_nooverwrite");
      checkResult(device->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 1, 0, 3, 6, 1), "Draw after Apply");
      checkResult(device->SetStreamSource(0, vertices.get(), 0, sizeof(Vertex)), "Set stream for updated tail");
      logDraw(frame, 6, "DrawPrimitive", "updated_vertex_tail");
      checkResult(device->DrawPrimitive(D3DPT_TRIANGLELIST, 5, 1), "Draw updated vertex tail");

      checkResult(device->SetVertexShader(nullptr), "Fixed function vertex shader");
      checkResult(device->SetPixelShader(nullptr), "Fixed function pixel shader");
      checkResult(device->SetFVF(D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_TEX1), "SetFVF");
      checkResult(device->SetTransform(D3DTS_WORLD, &identity), "SetTransform world");
      checkResult(device->SetTransform(D3DTS_VIEW, &identity), "SetTransform view");
      checkResult(device->SetTransform(D3DTS_PROJECTION, &identity), "SetTransform projection");
      D3DMATRIX translation = identity;
      translation._41 = 0.1f;
      checkResult(device->MultiplyTransform(D3DTS_WORLD, &translation), "MultiplyTransform");
      checkResult(device->SetTextureStageState(0, D3DTSS_COLOROP, D3DTOP_SELECTARG1), "TextureStage color op");
      checkResult(device->SetTextureStageState(0, D3DTSS_COLORARG1, D3DTA_TEXTURE), "TextureStage color arg");
      checkResult(device->SetTextureStageState(0, D3DTSS_ALPHAOP, D3DTOP_SELECTARG1), "TextureStage alpha op");
      checkResult(device->SetTextureStageState(0, D3DTSS_ALPHAARG1, D3DTA_TEXTURE), "TextureStage alpha arg");
      checkResult(device->SetTextureStageState(1, D3DTSS_COLOROP, D3DTOP_DISABLE), "Disable texture stage1");
      logDraw(frame, 7, "DrawPrimitive", "fixed_function_fvf");
      checkResult(device->DrawPrimitive(D3DPT_TRIANGLELIST, 2, 1), "Fixed function DrawPrimitive");
      checkResult(device->EndScene(), "EndScene");
      checkResult(device->Present(nullptr, nullptr, nullptr, nullptr), "Present");
      std::cout << "{\"probe_event\":\"present\",\"frame\":" << frame << "}\n";
    }
    std::cout << "{\"probe_event\":\"success\",\"frames\":" << frameCount
      << ",\"draws\":" << frameCount * kDrawsPerFrame << "}\n";
    return 0;
  }
}

int wmain(int argc, wchar_t** ppArguments) {
  try {
    uint32_t frames = 3;
    if (argc == 3 && std::wstring(ppArguments[1]) == L"--frames") {
      frames = uint32_t(std::stoul(ppArguments[2]));
    } else if (argc != 1) {
      throw std::runtime_error("Usage: edf_d3d9_probe.exe [--frames 1..16]");
    }
    if (!frames || frames > 16) {
      throw std::runtime_error("Frame count must be in the range 1..16");
    }
    return runProbe(frames);
  } catch (const std::exception& error) {
    std::cerr << "{\"probe_event\":\"error\",\"message\":\""
      << escapeJson(error.what()) << "\"}\n";
    return 1;
  }
}
