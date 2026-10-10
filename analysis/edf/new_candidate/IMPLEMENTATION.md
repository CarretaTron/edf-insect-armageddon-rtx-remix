# Implementação do protótipo estático EDF

O `EdfStaticDrawDecoder` foi implementado e compilado para a única candidata de **252 vértices utilizados / 144 triângulos / 432 índices**. Os dados originais das três recapturas passaram em replay GPU x64 direto e x86 pelo Bridge, com a mesma instance RTX, BLAS e TLAS opaca registradas. A comparação raster com o perfil desligado passou nos três frames de cada arquitetura. **O teste dentro do EDF permanece pendente.**

## Candidata e evidência espacial

A seleção, ranking, bytecodes, layout, hashes e resultados matemáticos estão em [REPORT.md](./REPORT.md). As três janelas são completas, sem truncamento, com 9.345 blobs verificados. A candidata aparece nos frames 755, 1182 e 1292, respectivamente nas draws 952, 952 e 898. Esses números não participam do reconhecimento.

A identidade é `925aa18481228b01392a574dbe28ba4e21aa1616`. O VS original é `f098f457d9ae603bea5f84ec985d473c29cfb0c9`; o PS original é `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`. Os cinco streams e os índices utilizados são byte a byte iguais nas três janelas. O shader comprova POSITION SHORT4 com escala 0,25, UV SHORT2 com escala 1/2048 e NORMAL UBYTE4 com escala 1/127 e bias -1. A declaração e a instrução que consome cada atributo fazem parte da validação.

Os registradores c0…c3 são a transformação composta local→clip. O decoder verifica a base c4…c6, usa a informação independente de câmera em c7 e fatoriza uma câmera RH e uma projeção perspectiva em uma âncora fixa. W identidade é a escolha dessa âncora; não comprova uma World original com translação zero. Não há decomposição única da origem global do engine.

Entre os primeiros dois frames há deriva de câmera pequena: 0,000073106 unidade e no máximo 0,0167663 pixel projetado. No terceiro, a câmera muda 1,164368768 unidades, 8,185000817° e até 253,197837 pixels. A mudança concorda com 16 pares independentes de transformação. A geometria, base e escalas permanecem idênticas. O erro máximo de reprojeção no decoder compilado é 0,000042776 pixel, abaixo do guard de 0,01 pixel.

A mesh contém seis componentes desconectados já incorporados aos vértices. O protótipo mantém **uma draw / uma mesh em lote / uma instance**, sem inventar seis entidades ou recuperar nomes do jogo.

## Alterações e motivo

| Arquivos principais | Problema e solução |
| --- | --- |
| `runtime/src/d3d9/d3d9_edf_static_decoder.h/.cpp` | Os atributos quantizados e constantes do VS não têm interpretação genérica suficiente. O decoder CPU verifica a whitelist, converte para FLOAT3 posição/normal e FLOAT2 UV, rebasa índices e valida a fatorização e a reprojeção. |
| `runtime/src/d3d9/d3d9_edf_static_profile.h/.cpp` | Restringe a ativação opt-in ao nome e SHA1 do executável. O processo pai é identificado quando existe Bridge. Defaults: desabilitado e rasterOnly=true. |
| `runtime/src/d3d9/d3d9_rtx.h/.cpp` | Integra a candidata antes dos filtros genéricos. Copia dados para staging próprio, cria RasterGeometry, material opaco com a textura s0 vinculada e DrawCallState com transforms validadas. Preserva a draw original. |
| `runtime/src/d3d9/d3d9_rtx_geometry.cpp` | Exclui bytecode/constantes VS móveis somente do hash da geometria já decodificada, mantendo identidade estável durante o movimento da câmera. O default genérico continua incluindo os dados anteriores. |
| `runtime/src/d3d9/d3d9_device.h/.cpp`, `d3d9_spec_constants.h`, `runtime/src/dxso/dxso_compiler.cpp` | A captura injetada no VS poderia acessar recursos de captura que o caminho CPU não prepara. Um specialization flag desliga o bloco inteiro apenas no perfil identificado. O default false mantém a captura genérica. Reset invalida o cache do flag. |
| `runtime/src/dxvk/dxvk_limits.h`, `dxvk_state_cache.cpp` | Acomoda o novo specialization flag. O layout atual de cache esparso é mantido; o cache v7 obsoleto, cujo layout dependia da contagem, é invalidado. |
| `runtime/src/dxvk/rtx_render/rtx_compatibility_trace.h/.cpp`, `rtx_types.h` | Marca exclusivamente a candidata e registra estágios com deduplicação e limite de frequência. Draws ordinárias usam trace ID zero. |
| `runtime/src/dxvk/rtx_render/rtx_context.h/.cpp`, `rtx_scene_manager.cpp`, `rtx_instance_manager.cpp`, `rtx_accel_manager.cpp` | Reutiliza câmera, cena, geometria, instance e estruturas de aceleração existentes. No modo rasterOnly prepara a cena e AS, mas não despacha path tracing nem substitui o raster. |
| `runtime/src/dxvk/meson.build` | Registra os novos arquivos na compilação. |
| `runtime/tools/edf_static_decoder_tests`, `runtime/tools/edf_static_gpu_replay` | Testes puros, replay real dos dados capturados, controle negativo, verificação do encadeamento RTX, comparação de pixels e proveniência do binário. |
| `runtime/tools/edf_capture/package.ps1` | Exclui backups de PDB dos bundles distribuídos, mantendo os backups de compilação no workspace. |

O validador SPIR-V encontrou três erros de tipo já presentes na revisão base, dentro da captura de vértices: armazenamento de vec4 normal em ponteiro vec3 e dois OpSelect de resultado vec4 com condição bool escalar em SPIR-V 1.3. Foram corrigidos apenas os tipos: shuffle xyz antes do store e replicação do bool em bool4 nos selects de custom transform e jitter. Os valores escolhidos e os defaults dessas opções não mudam. O VS e PS finais passam em `spirv-val --target-env vulkan1.3`: [spirv_validation.json](./spirv_validation.json).

Os logs de reconhecimento usam o contador de frame da fila CS que consome a draw, evitando associar trabalho enfileirado ao contador já avançado da thread D3D9.

## Caminho executado

```text
D3D9 DrawIndexedPrimitive
  → D3D9Rtx::internalPrepareDraw / prepareEdfStaticDraw
  → EdfStaticDrawDecoder / staging / RasterGeometry / hash / AABB
  → draw raster original + CommitGeometryToRT
  → RtxContext::commitGeometryToRT / CameraManager
  → SceneManager::submitDrawState / processGeometryInfo
  → InstanceManager::processSceneObject
  → AccelManager: BLAS / TLAS_Opaque
  → scene_only_frame, sem path tracing ou composição sobre o raster
```

O perfil aceita os SHA1 dos shaders, os conteúdos exatos dos cinco intervalos de atributos e do índice, a declaração/strides/frequencies, as constantes de escala/base, material opaco branco e descritores do passe/textura. Não usa endereço de shader, número de draw ou UID de buffer. Buffers com um byte de POSITION diferente ficam no raster sem conversão. Outras meshes e shaders do EDF não são reconstruídos por este perfil.

## Resultados com o binário final

Runtime SHA256: `7b8ab28eee0838ee2e9e018663e7f911df6d311d7b25437587fb5cffc7cdb98d`, 188.300.800 bytes. Base `0e18e55c04309c08e6206e768bc72b24ac83f853`, build debugoptimized x64. EDF continua sendo x86 e recebe o cliente Bridge correspondente.

| Verificação | Resultado e alcance |
| --- | --- |
| Decoder CPU compilado MSVC | Três frames aprovados e 36 guards negativos exercitados. |
| Matrix4/decomposeProjection reais | Três casos aprovados; erro float máximo abaixo de 0,0001 pixel. |
| Replay GPU x64 | Três frames, 842 HRESULTs aprovados, controle alterado rejeitado e candidata aceita. [verification.json](./gpu_x64/verification.json). |
| Replay GPU x86 → Bridge → x64 | Mesmos três frames e 842 HRESULTs aprovados, inclusive transporte dos cinco streams e constantes. [verification.json](./gpu_x86/verification.json). |
| Instance / geometria | Instance ID 1, mask 8, hidden=0, 252 vértices / 432 índices, hashes de posição/índice/UV/geometria/material estáveis nos três frames. |
| BLAS / TLAS | Build de BLAS registrado no primeiro frame, reutilização nos seguintes; a mesma BLAS incluída na TLAS_Opaque nos três frames. |
| Raster x64 e x86 | BMPs de 1920×1080 não uniformes, bytes e pixels idênticos entre baseline e perfil nos três frames. [x64](./gpu_x64/raster_comparison.json), [x86](./gpu_x86/raster_comparison.json). |
| SPIR-V original traduzido | VS e PS finais aprovados. |
| Proveniência dos verificadores | Evidência final vinculada ao SHA256/tamanho do renderer efetivamente usado. No x86 é `.trex/d3d9.dll`, não o proxy do jogo. 14 testes de proveniência, 22 testes do verificador e 10 do comparador são fixtures sintéticas explicitamente rotuladas. |
| Pacotes | Arquivos/tamanhos/SHA256 conferidos em [package_verification.json](./package_verification.json). |

Os logs completos finais estão em [gpu_x64/remix-dxvk.log](./gpu_x64/remix-dxvk.log) e [gpu_x86/remix-dxvk.log](./gpu_x86/remix-dxvk.log). Os marcadores `blas_recorded`/`tlas_recorded` são emitidos após o registro dos comandos Vulkan; não comprovam isoladamente conclusão por fence, resultado de raios ou correspondência visual dentro do EDF.

O replay usa os buffers, índices, bytecodes, constantes e estados capturados, mas **texturas sintéticas**. A textura base original é DXT5 128×128/oito mips; seus pixels não foram capturados. O adapter usa s0 já vinculado ao jogo. A igualdade raster aqui comprova a preservação no replay, não a aparência da textura ou iluminação reais.

Os quatro replays encerraram com código zero e sem falha de HRESULT. Há mensagens NGX de fallback ao carregar bibliotecas e avisos debug `common device objects were not disposed of` / `is alive at exit`: 40 objetos no baseline e 43 no perfil, nas duas arquiteturas. Portanto os logs não são livres de erros/avisos de encerramento. Essa observação está preservada em [replay_shutdown_observations.json](./replay_shutdown_observations.json); não foi atribuído ao EDF um comportamento ainda não testado. A contagem zero de mensagens de validation layer não significa que uma layer Vulkan de validação tenha sido habilitada.

## Pacote, teste e limites

A validação consolidada está em [next_validation/validation.json](../next_validation/validation.json), com a antiga candidata preservada no histórico. Os bundles finais ficam em `packages/edf-static-prototype/edf-diagnostics-x86` e `edf-diagnostics-x64`. O pacote não foi instalado automaticamente no EDF.

[TESTAR.md](../../../profiles/edf-static-prototype/TESTAR.md) contém verificação do manifesto, Restore do pacote anterior, instalação com backup, teste na mesma missão/região, busca dos marcadores e Restore final. Use o bundle x86 e o perfil `profiles/edf-static-prototype/rtx.conf` com o fingerprint real do EDF, não os perfis dos replays.

Ainda dependem do jogo: reconhecimento na missão real, raster dos passes posteriores preservado, estabilidade durante movimentação prolongada e aparência/UV com texels originais. Replacement, path tracing, skinning, personagens e efeitos ficam para etapas seguintes. Não foi executado outro jogo de controle; o isolamento de outros executáveis foi verificado no código e nos guards, sem afirmar validação empírica de todos os jogos.

A compilação local precisou separar o PDB do compilador do PDB do linker no backend Meson para evitar conflito de abertura em rebuild. Isso é uma alteração local da ferramenta, documentada em `.tools/build-python/MESON_PDB_WORKAROUND.md`, sem regra de compatibilidade no Runtime. As opções foram regeneradas por `DXVK_DOCUMENTATION_WRITE_RTX_OPTIONS_MD=1` no replay final e copiadas para `runtime/RtxOptions.md`.
