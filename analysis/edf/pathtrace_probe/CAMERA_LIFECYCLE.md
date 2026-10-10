# EDF: câmera, frames e ponto de composição

Análise do código em 7 de outubro de 2026 e do log original arquivado em `actual_edf_runtime.log`. Esta análise não usa a ausência de linhas limitadas por frequência como prova de ausência de uma draw.

## Caminho do frame

1. `D3D9Rtx::internalPrepareDraw` entra em `prepareEdfStaticDraw` antes dos filtros genéricos quando o executável e o perfil EDF correspondem. As draws desconhecidas mantêm `PreserveDrawCallAndItsState`; a única mesh permitida acrescenta `CommitToRayTracing`.
2. O reconhecimento e `commitGeometryToRT` são comandos na mesma fila CS. Ambos consultam `DxvkDevice::getCurrentFrameId()` no momento da execução. O contador é `DxvkStatCounter::QueuePresentCount`, e não o índice de draw nem o contador de `Present` do thread D3D9.
3. `RtxContext::commitGeometryToRT` conclui os futures de geometria, chama `CameraManager::processCameraData` e submete a draw ao `SceneManager`.
4. `CameraManager::processCameraData` classifica a câmera, decompõe a projeção e chama `RtCamera::update`. `RtCamera::update` aceita somente a primeira atualização de uma câmera no mesmo frame. Ao terminar, define `m_frameLastTouched=frameIdx`.
5. `D3D9SwapChainEx::Present` chama `D3D9Rtx::EndFrame` antes de verificar a disponibilidade da swapchain. `EndFrame` descarrega os comandos pendentes e enfileira `RtxContext::endFrame`, que tenta a injeção de fallback.
6. `injectRTX` consulta a câmera Main no mesmo contador de apresentação. `RtCamera::isValid(frame)` significa exatamente `m_frameLastTouched==frame`. Uma câmera válida no frame anterior não é uma câmera válida no frame atual.
7. Depois dos comandos do jogo e da injeção, `SubmitPresent` enfileira a apresentação. `DxvkDevice::presentImage` incrementa `QueuePresentCount`. Um retorno anterior a `PresentImage` não incrementa esse contador.

As heurísticas genéricas de UI, `makeDrawCallType` e `triggerInjectRTX` não são o ponto de injeção normal deste perfil: a entrada específica EDF retorna antes delas. O ponto confirmado pelo código é o fallback de `Present`.

## `main_camera_invalid`

O perfil extrai câmera somente da mesh permitida. Manter uma instance ou BLAS em cache não atualiza a câmera. Portanto, um frame em que a mesh foi eliminada pelo jogo, não enviada ou rejeitada por algum guard do decoder pode terminar com uma câmera Main antiga e receber essa rejeição normalmente.

O log contém câmeras aceitas nos frames 534–541, 694, 961, 1081, 1205 e 1371. As rejeições de câmera aparecem nos frames 656, 799, 1108, 1215, 1266 e 1385. Não há linha que mostre `camera_accepted` e `main_camera_invalid` no mesmo frame.

Essa observação não identifica, sozinha, a causa de cada ausência. `shouldTraceCompatibilityDraw` emite os primeiros oito frames de cada stage e depois limita a frequência a um intervalo de 120 frames. A instrumentação anterior tampouco registra o último frame atualizado da câmera na tentativa de injeção, a contagem de draws permitidas daquele frame nem os motivos de todos os guards. As informações necessárias são:

- Frame da fila, origem e número da tentativa de injeção.
- Último frame da draw permitida, último frame de aceitação da câmera e quantidade de draws permitidas neste frame.
- `Main.getLastUpdateFrame()`, validade e hashes das matrizes efetivamente usadas.
- Contagens independentes do limitador dos logs para frames com e sem a candidata.
- Se houver draw permitida no frame com câmera inválida, resultado exato de `processCameraData` e estado do frame entre o commit e a injeção.

Não há evidência suficiente para relaxar a validade, copiar a câmera anterior ou modificar a classificação Main. Isso produziria uma câmera desatualizada quando o jogador se move e esconderia a informação que está faltando.

## `already_injected_this_frame`

O guard é a igualdade exata entre `m_frameLastInjected` e `QueuePresentCount`. Ele é anterior à execução repetida de `prepareSceneData`, à construção de aceleração e ao path tracing. É a proteção esperada contra uma segunda tentativa no mesmo frame da fila.

No log há uma ocorrência no frame 795 às 22:26:24.623. Ela antecede uma pausa de aproximadamente 11 segundos e a recriação da swapchain às 22:26:35.841. Isso é compatível com apresentações sem avanço do contador durante indisponibilidade/recriação da swapchain. O código permite esse caminho porque `EndFrame` ocorre antes da verificação `hasSwapChain()`.

A causa exata dessa ocorrência permanece uma hipótese: o log anterior não contém a origem da tentativa nem um serial de `EndFrame`/`Present`. O significado comprovado da linha é a segunda tentativa no mesmo contador, não uma câmera defeituosa. O guard já existe na revisão oficial fixada. Não deve ser removido.

`SceneManager::onFrameEnd` limpa `CameraManager::m_lastSetCameraType` e o cache de decomposição; não apaga `RtCamera::m_frameLastTouched`. A validade muda quando o contador de apresentação avança.

## Câmera que o renderizador usa

`SceneManager::getCamera()` retorna a câmera Main. `RtxContext::updateRaytraceArgsConstantBuffer` copia `camera.getShaderConstants()` para `RaytraceArgs::camera`.

`RtCamera::getShaderConstants(bool freecam=true)` usa:

| Campo | Origem |
|---|---|
| `worldToView` | `getWorldToView(true)`: free camera quando habilitada; caso contrário, matriz Main normal. |
| `viewToProjection` | `getViewToProjection()`, projeção sem jitter, já sujeita às opções de correção/near plane. |
| `viewToProjectionJittered` | Cache da projeção com jitter calculado pelo Runtime. |
| `worldToProjectionJittered` | `viewToProjectionJittered * worldToView`. |
| `projectionToWorldJittered` | `viewToWorld * projectionToViewJittered`. |

O teste de câmera deve comparar as matrizes decodificadas com **as matrizes dos shader constants**, além de registrar `getWorldToView(false)`. Comparar apenas os dados CPU entregues a `CameraManager` não prova a câmera usada na GPU.

O jitter é controlado por `calcPixelJitter`: DLSS/RR, XeSS, TAA ou `forceCameraJitter` habilitam o deslocamento. `applyAndGetJitter` altera `P[2][0]` e `P[2][1]`, proporcionalmente a `2*jitterPixel/resolução`, com sinais derivados de `P[2][3]`. Uma diferença nesses termos deve ser validada contra o jitter conhecido; não deve ser tratada automaticamente como um erro de câmera. Para o primeiro experimento, sem upscaler e sem jitter, as projeções podem coincidir diretamente.

As opções de free camera, `freeCameraViewRelative`, shake, inversão Y da projeção, override de near plane e reprodução de uma sequência de câmera podem modificar as matrizes. O experimento deve rejeitar uma câmera diferente da interpretação comprovada, ou registrar e validar explicitamente a modificação. Não deve usar silenciosamente as matrizes do decoder como prova dos shader constants.

## Composição e execução GPU

A mesh original é renderizada num MRT opaco fora da tela. O alvo passado para `EndFrame` é o backbuffer do jogo. Ao chegar a esse ponto, o EDF já submeteu seus próprios passes de cenário, materiais, efeitos e composição.

O renderer RTX normal executa `dispatchPathTracing`, `dispatchComposite` e pós-processamento em recursos próprios, depois `blitImageHelper` copia **a imagem inteira** para esse alvo. Com apenas uma mesh reconstruída, essa cópia substituiria o restante do conteúdo do EDF. O ponto de injeção existente não constitui prova de um ponto correto para substituir somente a mesh.

O próximo experimento deve manter a draw original e o backbuffer intactos e gerar o resultado RTX em recursos separados. Deve exigir a draw e a câmera no frame atual, confirmar que a cena contém somente a instance permitida, exportar resultados GPU e impedir o blit para o jogo e a geração de frames. Não existe substituição visual parcial autorizada por esta análise.

`AssetExporter::exportImage` já oferece readback sincronizado: registra cópia/barreira para uma imagem linear acessível à CPU, sinaliza uma `sync::Fence` e o worker aguarda essa fence antes de ler e gravar o DDS. Arquivos exportados completos de profundidade/normais/albedo e resultado RTX oferecem evidência de execução e de saídas GPU; um log de dispatch isolado mostra apenas que comandos foram registrados. O arquivo deve identificar o frame exato para evitar misturar saídas de tentativas diferentes.

A preservação dos outros passes deve ser testada comparando os pixels apresentados com o modo offscreen desligado/ligado em replays controlados e por readback do backbuffer antes/depois da injeção. Isso não demonstra, por si só, que os efeitos internos do jogo compreenderiam uma futura substituição de MRT; essa investigação fica para depois.

## Guard implementado nesta etapa

`D3D9Rtx::prepareEdfStaticDraw` agora define `compatibilityRasterOnly=true` incondicionalmente para a candidata. O valor legado `rtx.edfStatic.rasterOnly=false` recebe um aviso uma vez e não autoriza substituição da imagem do jogo. A descrição da opção foi corrigida. O opt-in do experimento offscreen é separado desse valor legado.

O reconhecimento por hash, guards de layout/passe e flags das demais draws permanecem iguais. Esta mudança fecha um caminho de configuração que antes permitia entrar no blit completo do renderer com apenas uma mesh, incompatível com o escopo pedido pelo usuário. Ela não corrige nem disfarça as rejeições normais de câmera/duplicação.

## Fontes centrais

- `runtime/src/d3d9/d3d9_rtx.cpp`: `internalPrepareDraw`, `prepareEdfStaticDraw`, `CommitGeometryToRT`, `triggerInjectRTX`, `EndFrame`.
- `runtime/src/d3d9/d3d9_swapchain.cpp`: `Present`, `PresentImage`, `SubmitPresent`; `runtime/src/dxvk/dxvk_device.cpp`: `getCurrentFrameId`, `presentImage`, `incrementPresentCount`.
- `runtime/src/dxvk/rtx_render/rtx_camera_manager.cpp`: `processCameraData`, `isCameraValid`, `onFrameEnd`.
- `runtime/src/dxvk/rtx_render/rtx_camera.cpp`: `update`, `getWorldToView`, `getShaderConstants`, `calcPixelJitter`, `applyAndGetJitter`; `rtx_camera.h`: `isValid`, `getLastUpdateFrame`.
- `runtime/src/dxvk/rtx_render/rtx_context.cpp`: `commitGeometryToRT`, `injectRTX`, `endFrame`, `updateRaytraceArgsConstantBuffer`, `onInjectRtxFrameEnd`.
- `runtime/src/dxvk/rtx_render/rtx_scene_manager.cpp`: `onFrameEnd`; `rtx_compatibility_trace.cpp`: `shouldTraceCompatibilityDraw`.
- `runtime/src/dxvk/rtx_render/rtx_asset_exporter.cpp`: `exportImage`.

As regras genéricas de validade e de duplicação foram conferidas também na revisão oficial `0e18e55c04309c08e6206e768bc72b24ac83f853`. Não foram introduzidas pelo decoder EDF.
