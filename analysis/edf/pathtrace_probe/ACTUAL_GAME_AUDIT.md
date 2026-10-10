# Execução real do protótipo no EDF

O log confirma que a mesh restrita de **252 vértices / 144 triângulos** chegou ao caminho RTX durante gameplay real. Confirma a gravação de comandos BLAS/TLAS e a inclusão da instance na TLAS opaca. Ainda não demonstra path tracing, composição visual ou conclusão GPU dessas estruturas por um teste dedicado.

Evidência preservada: [actual_edf_runtime.log](actual_edf_runtime.log), 67.921 bytes, SHA256 `ab56ea7ccf54b38f960814862e1489ea9cddad43a3151ec04cc13dbad032ad5f`. Origem: `D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon\rtx-remix\logs\remix-dxvk.log`. O snapshot tem 600 linhas, de 22:25:34.907 a 22:26:55.553 de 07/10/2026. O log termina sem resultado explícito de encerramento do processo.

Auditoria reproduzível: [actual_edf_log_audit.json](actual_edf_log_audit.json), produzida por [audit_actual_log.py](audit_actual_log.py). Esta análise não usa capturas antigas como se fossem desta execução.

## O que está efetivamente demonstrado

O perfil validou o SHA1 do executável `d2e90641c560420d279a629ddd86e13cb767f464` e selecionou `925aa18481228b01392a574dbe28ba4e21aa1616`. O par de shaders registrado é VS `f098f457d9ae603bea5f84ec985d473c29cfb0c9` / PS `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`. `rasterOnly=True` aparece na configuração efetiva.

| Evento | Amostras registradas |
|---|---:|
| recognized / raster_geometry / camera_accepted | 13 de cada |
| scene_geometry / instance_accepted | 9 de cada |
| blas_recorded | 6 |
| blas_reused / tlas_recorded / scene_only_frame / instance_preserved | 13 de cada |
| injection_rejected: main_camera_invalid | 6 |
| injection_rejected: already_injected_this_frame | 1 |

Há cadeias completas de criação nos frames **534, 694, 961, 1205 e 1371**. As linhas 472–480 documentam a primeira: draw 874, câmera Main (`type=0`, `valid=1`), instance 1 com `mask=8` e `hidden=0`, BLAS e TLAS opaca com `included=1`. As linhas 481–495 mostram atualização e reutilização subsequentes. A BLAS também foi registrada para instance 5 no frame 1263; as demais etapas desse frame não foram emitidas pelo log limitado.

Os hashes de geometria permanecem únicos em todas as amostras:

| Dado | Hash |
|---|---|
| Posições | `0x6d7756d3182eed1d` |
| Índices | `0xdb9f5782ee579d47` |
| UV | `0x78eb56661f89659b` |
| Geometria | `0x32aeb7bb7e772c13` |
| Material / textura base real | `0x2d5d4d9853fd5eac` |

O material real difere da textura sintética dos probes anteriores. O decoder preservou FLOAT3 posição/normal, FLOAT2 UV, stride 32 e índices rebased 0..251. As 13 validações de reprojeção variam entre **0,00000933907 e 0,0000978515 pixel**. Os anchors de câmera mudam durante gameplay. Isso confirma os cálculos e a aceitação interna; uma sobreposição visual ainda é necessária para testar a projeção usada no ponto final de composição.

Os IDs de instance observados são 1..6. O novo ID após intervalos não significa mudança de geometria: os hashes permanecem idênticos. O log não informa o motivo exato de cada coleta/recriação.

| Milestone | Conclusão limitada à evidência |
|---|---|
| M1 | Carregamento e funcionamento durante o intervalo registrado, com gameplay real. O log não contém confirmação de encerramento limpo. |
| M2 | A draw restrita foi detectada repetidamente. Não há suporte geral às draws do jogo. |
| M3 | Mesh decodificada aceita em SceneManager/InstanceManager e comandos BLAS/TLAS registrados. A aparência reconstruída ainda não foi exibida. |
| M4 | Câmera Main aceita em vários frames; reprojeção matemática subpixel e geometria estável. Validade no ponto de injeção e sobreposição visual ainda precisam de correlação por frame. |
| M5 | Textura/material reais vinculados e hash estável. UV, cor e material path traced ainda não foram validados visualmente. |
| M6 | Replacement não exercitado. |
| M7 | Não demonstrado: todas as amostras `scene_only_frame` dizem `path_tracing_dispatched=0`. |
| M8–M10 | Fora do escopo da mesh estática restrita. |

## Rejeições da câmera

| Frame rejeitado | Motivo | Última câmera aceita **registrada** | Próxima câmera aceita **registrada** |
|---:|---|---:|---:|
| 656 | main_camera_invalid | 541 | 694 |
| 795 | already_injected_this_frame | 694 | 961 |
| 799 | main_camera_invalid | 694 | 961 |
| 1108 | main_camera_invalid | 1081 | 1205 |
| 1215 | main_camera_invalid | 1205 | 1371 |
| 1266 | main_camera_invalid | 1205 | 1371 |
| 1385 | main_camera_invalid | 1371 | Sem amostra posterior |

Nenhum frame `main_camera_invalid` tem uma `camera_accepted` emitida no mesmo frame. Isso **não prova ausência da draw**, pois o logger limita as mensagens por estágio: emite os primeiros oito frames únicos e depois somente após um intervalo mínimo de 120 frames. Também elimina repetições de um estágio no mesmo frame. Os números acima são contagens de mensagens, não contadores completos de atividade.

A implementação de `RtCamera::isValid(frameIdx)` exige `m_frameLastTouched == frameIdx` (`src/dxvk/rtx_render/rtx_camera.h:291`). A injeção verifica a câmera Main com o frame atual do device (`rtx_context.cpp:512`). `CameraManager::processCameraData` atualiza a câmera no frame em que recebe uma draw válida; `RtCamera::update` ignora uma segunda atualização no mesmo frame (`rtx_camera.cpp:637`). Portanto, uma aceitação no frame anterior não mantém a câmera válida para o seguinte.

No perfil restrito, `D3D9Rtx::internalPrepareDraw` entrega somente a candidata ao caminho RTX e preserva outras draws (`d3d9_rtx.cpp:617`). Se a candidata não estiver presente ou for rejeitada por alguma guarda, o perfil não possui outra fonte validada de câmera nesse frame. Nesse caso, rejeitar a injeção é coerente. Também é possível uma tentativa de injeção ocorrer antes do commit da candidata. O log atual não distingue esses casos: faltam o frame da última atualização, os commits/rejeições por frame, a origem da tentativa e o render target associado.

O frame 799 sucede uma recriação do swapchain documentada às 22:26:35.841, linhas 550–555 (1920×1080, FIFO). Essa correlação temporal merece cobertura no próximo teste, mas não comprova que a recriação causou a rejeição.

Não há evidência neste log que justifique manter a câmera de um frame antigo, retirar a checagem de validade ou alimentar o renderer com matrizes adivinhadas.

## Duplicação da injeção

O único `already_injected_this_frame` é do frame **795**, linha 549. No mesmo frame, linhas 547–548, a mesh aparece em `scene_geometry` e `instance_accepted`, com o mesmo hash e instance 2.

O guarda existente rejeita a tentativa quando `m_frameLastInjected == currentFrameId` (`rtx_context.cpp:557`). Isso evita uma segunda injeção no frame; a mensagem não indica uma falha de câmera. O log não registra a origem ou o target da primeira tentativa desse frame, pois as mensagens de sucesso também estão limitadas. Assim, a rejeição duplicada é normal enquanto a primeira tentativa ocorre no ponto correto; ainda precisamos verificar se houve uma primeira injeção antes do passe relevante do EDF. Não há base para remover o guarda de duplicação.

## Dados faltantes para composição e execução GPU

Não existe `D:\EDF IA RTX REMIX\captures\edf-prototype` no momento da auditoria. O log não contém disparo ou conclusão de uma nova janela de captura. As capturas antigas `edf` / `edf-static` pertencem à validação anterior e não substituem a observação da ordem de passes desta execução.

Antes de substituir visualmente a mesh, precisamos registrar no mesmo frame:

- a ordem do commit selecionado e de cada tentativa de injeção, sua origem e o `currentFrameId`;
- `lastCameraUpdateFrame`, tipo, hashes de view/projection e validade da câmera usada na injeção;
- imagem/target da tentativa, dimensões/formato/subresource e sua relação com o MRT original, backbuffer e etapa de apresentação;
- dispatch controlado limitado à mesh e prova de conclusão GPU, sem escrever no destino visual do EDF;
- preservação dos demais passes com captura/readback comparável.

O log não contém erro de Runtime sob o marcador de severidade `err/error`. Há mensagens opcionais de carregamento NGX e indisponibilidade DLSS-NR, além de QueryInterface desconhecida. Essas mensagens não explicam a perda de validade da câmera. Ausência de erro não é um teste de validação Vulkan nem prova de execução GPU.

Este relatório audita somente a evidência real recebida. As correções e os resultados do próximo probe devem ser documentados separadamente.
