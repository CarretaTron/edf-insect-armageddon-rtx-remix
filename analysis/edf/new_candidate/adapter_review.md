# Revisão independente do adapter e perfil EDF

## Problema de captura do VS encontrado e corrigido

O caminho CPU cria `RasterGeometry` sem chamar `prepareVertexCapture`. Mesmo assim, a tradução DXSO original chama `emitVertexCaptureOp` para todo vertex shader. Nessa revisão, não existe o método `UseVertexCaptureSetSpecConstant` mencionado na hipótese inicial: a opção global `useVertexCapture` controla a preparação de buffers no caminho D3D9, sem desligar por si só o código injetado no VS.

`emitVertexCaptureInit` declara o specialization bool `vertex_capture_out_bound`, mas o corpo original de `emitVertexCaptureOp` não o consulta. A saída usa stores no SSBO e lê constantes próprias da captura. Portanto, somente deixar de preparar ou desvincular o SSBO não constitui uma garantia de ausência dessas operações. Um buffer antigo ou o recurso dummy pode continuar participando do shader.

A correção autorizada acrescenta `D3D9SpecConstantId::VertexCaptureDisabled=14`, com default false. O corpo completo de `emitVertexCaptureOp` executa apenas quando esse flag é false. Isso inclui stores, custom transform e jitter. `D3D9DeviceEx::PrepareDraw` estabelece o flag a partir do perfil opt-in já validado pelo fingerprint do executável. Todas as draws raster do modo isolado ficam com a captura desligada; shaders e draws de outros jogos usam false e mantêm o caminho anterior.

O valor é guardado em `std::optional<bool> m_lastVertexCaptureDisabled`: uma emissão na primeira draw, após Reset ou quando o modo muda. `ResetDeviceState` invalida o cache. Assim, não acrescenta um comando CS em cada draw ordinária. `git diff --check` passou. Compilação e teste GPU após essa alteração ficam a cargo da etapa de implementação.

Arquivos dessa correção:

- `src/d3d9/d3d9_spec_constants.h`
- `src/d3d9/d3d9_device.h`
- `src/d3d9/d3d9_device.cpp`
- `src/dxso/dxso_compiler.cpp`

O teste GPU deve usar perfil ativo **e** `rtx.useVertexCapture=true`, comparando a saída raster com o perfil desativado. Usar apenas a opção global false não exercita a condição encontrada. Reset seguido de nova draw deve restabelecer a especialização correta.

## Demais verificações do adapter

- As leituras de índices e streams usam offsets e somas em uint64, com teste do intervalo contra o mapped slice antes do acesso.
- O intervalo raw 630…881 e seus 432 índices são reconhecidos pelo conteúdo, não pelo StartIndex ou UID.
- Os cinco SHA1 de atributos e os bytecodes restringem o caminho à candidata selecionada.
- Os elementos, formatos, strides e frequências são verificados. UP e streams instanciados não entram nesse decoder.
- As cópias dos vértices e índices possuem staging próprio e referências `RasterBuffer`. O `acquire(Read)` antes da segunda alocação impede a reutilização da primeira faixa pelo allocator. O hash assíncrono obtém suas próprias referências antes de ler.
- `computeHash(...,false)` exclui constantes móveis de câmera somente dessa geometria CPU. O default do caminho genérico permanece true.
- A conversão de matrizes D3D row-vector para `Matrix4` está coerente: a composição interna é Projection × View × World. `objectToView` usa View × World.
- A geometria é FLOAT3/FLOAT3/FLOAT2, permitindo o caminho existente de conversão, AABB e buffers RTX.
- Os estados opacos, targets MRT e depth/extent guardam o passe selecionado. Alpha do material é explicitamente ignorado, coerente com `oC0.w=1` do PS.
- `CommitToRayTracing | PreserveDrawCallAndItsState` mantém a emissão da draw original, necessária para os MRTs do raster.

Não foi encontrado outro bloqueio de alta prioridade no adapter revisado. O log que rotulava `material.getHash()` como `texture_hash` foi reportado ao implementador para corrigir a nomenclatura; o hash de material completo não é o hash exclusivo da textura.

Esta revisão de código não substitui a confirmação de CameraManager, SceneManager, InstanceManager, build BLAS/TLAS e raster preservado no teste GPU e no jogo.
