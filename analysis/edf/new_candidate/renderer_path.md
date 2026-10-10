# Rastreamento do protótipo no renderer

O decoder atribui `DrawCallState.compatibilityTraceId = 0x4544465354415449` somente depois de passar pelo perfil e pela allowlist de conteúdo. O valor padrão é zero. O marcador não faz parte dos dados enviados aos shaders nem dos hashes geométricos ou materiais.

`BlasEntry::input` já armazena a draw. O update dinâmico copia `DrawCallState`, e o caminho de preservação também atualiza esse estado antes de preservar a instance. `RtInstance::getBlas()` permite recuperar o marcador. Não foram adicionados campos a `RasterGeometry`, `RaytraceGeometry`, `RtInstance`, BLAS GPU ou TLAS GPU.

## Etapas observáveis

O prefixo dos logs é `[RTX-Compatibility-Trace]`. Cada registro contém `trace`, `stage` e `frame`.

| Stage | Evidência registrada |
|---|---|
| `recognized` | O adapter D3D9 validou a draw específica. |
| `raster_geometry` | O adapter criou os buffers decodificados. |
| `camera_accepted` | `CameraManager` processou a projeção e a câmera está válida neste frame. |
| `camera_rejected` | Não existe câmera aceita/validada; o registro informa a condição. |
| `scene_geometry` | `SceneManager` processou o cache e produziu a `RaytraceGeometry`, com contagens e formatos. |
| `scene_rejected` | O caminho encontrou ausência de posição, quantidade zero, falha no cache de índices, material ignorado, limite do cache, falha de futures ou sky skip. |
| `instance_accepted` | `InstanceManager::processSceneObject` criou/atualizou a instance e retornou depois de `updateInstance`. Inclui ID, máscara, estado hidden e material ID. |
| `instance_preserved` | O caminho de preservação reutilizou e atualizou a instance. |
| `blas_recorded` | Foi gravada uma chamada real `vkCmdBuildAccelerationStructuresKHR`, e o `dstAccelerationStructure` dessa chamada corresponde ao BLAS da geometria marcada. Inclui modo build/update e endereço do BLAS. |
| `blas_reused` | O cache reutiliza a cena e o BLAS existente, sem alegar nova construção. |
| `blas_rejected` | A instance está hidden ou tem máscara zero, impedindo a inclusão geométrica normal. |
| `tlas_recorded` | Após gravar a construção real do TLAS, o endereço do BLAS marcado está presente em uma instance com máscara não zero na lista usada pela chamada. |
| `tlas_rejected` | Uma superfície marcada existe, mas o endereço de BLAS não foi encontrado no TLAS esperado. |
| `injection_rejected` | A preparação/renderização normal foi impedida por falta de suporte RT, câmera inválida, compilação assíncrona, RT desabilitado ou frame já injetado. |
| `scene_only_frame` | A preparação normal da cena terminou, mas path tracing e blit para o jogo foram omitidos para preservar a imagem raster. |

`blas_recorded` e `tlas_recorded` comprovam a gravação dos comandos Vulkan com associação aos recursos corretos. Não significam que uma fence já confirmou o término da execução GPU ou que a silhueta renderizada foi comparada ao jogo. Um teste com término normal e sem erros do dispositivo fornece a evidência operacional adicional.

Os estágios são limitados separadamente por ID: um log por frame nos primeiros oito frames observados, depois um log a cada 120 frames. O estado é protegido por mutex e limitado a 64 IDs. O marcador zero retorna antes de alocar estado. As buscas adicionais na lista de construções são desativadas enquanto nenhum marcador válido foi observado.

## Material opaco específico

Na conversão padrão de `LegacyMaterialData` para `OpaqueMaterialData`, somente o marcador EDF validado recebe `setIgnoreAlphaChannel(true)`. A evidência é a saída `oC0.a = 1` do pixel shader validado. A alpha da textura s0 participa de uma mistura de material no shader original e não representa transparência dessa draw. Overrides e replacements explícitos mantêm suas próprias regras de material.

## Preparação e bloqueios

O caminho normal `RtxContext::injectRTX` chama `SceneManager::prepareSceneData` apenas se RT está habilitado, a câmera main está válida e não existe compilação assíncrona pendente. A configuração de captura com RT desabilitado fornece evidência de dados D3D9 e não comprova BLAS/TLAS.

O passe EDF validado escreve MRTs offscreen com o mesmo tamanho do backbuffer. A classificação genérica compara dimensões para determinar um alvo primário. Ela não deve ser usada para interpretar o passe no protótipo: o adapter aplica sua validação de passe e preserva a draw raster original. Draws não reconhecidas permanecem no raster.

O primeiro fullscreen/UI pode disparar a injeção genérica antes do Present. O perfil opt-in evita essa classificação para draws não reconhecidas e faz a preparação no Present depois de receber a geometria escolhida. `DrawCallState.compatibilityRasterOnly`, combinado com o marcador não zero, permite executar a preparação normal de cena/AS dentro de `injectRTX`, omitindo os dispatches de path tracing/composição e o blit ao jogo. O flag interno é limpo ao terminar a injeção. A imagem raster original permanece durante a validação inicial.

## Arquivos modificados

- `src/dxvk/rtx_render/rtx_compatibility_trace.h/.cpp`: nomes de estágio, helper e limitador.
- `src/dxvk/rtx_render/rtx_types.h`: marcador de draw.
- `src/dxvk/rtx_render/rtx_context.h/.cpp`: câmera, futures, sky skip, rejeições de injeção e observação da cena/AS sem alterar a imagem final.
- `src/dxvk/rtx_render/rtx_scene_manager.cpp`: geometria convertida, rejeições, preservação e alpha específica.
- `src/dxvk/rtx_render/rtx_instance_manager.cpp`: aceitação da instance.
- `src/dxvk/rtx_render/rtx_accel_manager.cpp`: associação aos comandos reais de BLAS/TLAS e cache.
- `src/dxvk/meson.build`: registro dos helpers e componentes isolados do decoder/perfil.

## Verificação do limitador

`verify_trace_limiter.cpp` compila e executa o código real do helper. `trace_limiter_verification.json` registra marcador desabilitado, estágio inválido, deduplicação por frame, intervalo, independência entre estágios/IDs e 16 callers concorrentes produzindo somente um registro permitido. Esta verificação não exercita o renderer GPU. A compilação completa e o teste controlado do Runtime são registrados no relatório principal.

## Verificação do replay GPU

`tools/edf_static_gpu_replay/verify_runtime.py <diretório-do-replay>` grava `verification.json` e retorna código 0 somente se todos os critérios passarem. Ele exige resultado normal do programa de replay, mapeamento explícito dos frames de replay para 755/1182/1292, uma draw controle e uma draw positiva por frame, flags 6/7, integridade de todas as referências/blobs/footer, shaders e cinco streams originais, bancos de constantes float correspondentes ao frame de origem, câmera main válida e evidência de instance/SceneManager/BLAS/TLAS/raster-only.

O caminho de cache é aceito: a geometria pode ser comprovada por `scene_geometry` no primeiro frame e `instance_preserved` nos seguintes. Os hashes de posição, índices, UV, geometria completa e material precisam permanecer estáveis, assim como o ID da instance. A lista TLAS precisa referenciar um endereço BLAS que tenha construção registrada. Qualquer estágio de rejeição falha a verificação. Logs `blas_reused` nunca substituem a necessidade de pelo menos uma construção real inicial.

O tool encontra `*.log` e `records.jsonl` recursivamente. `--runtime-log`, `--capture` e `--stdout` selecionam caminhos explícitos. `--runtime-frame-offset` e `--capture-frame-offset` permitem informar diferenças de contagem conhecidas do renderer e do observador, separadamente; elas não são inferidas. A configuração de captura para o replay precisa registrar as duas draws em todos os três primeiros frames.

`runtime_verifier_unit_tests.json` registra testes do próprio verificador com logs sintéticos e payloads D3D9 capturados. Esses fixtures, marcados como `UNIT_TEST_FIXTURE_ONLY`, não representam execução GPU. O resultado JSON inclui `fixtureOnly=true`, `notGpuEvidence=true` e `evidenceKind=synthetic_verifier_fixture`. Os 22 testes incluem rejeição de câmera ausente, reconhecimento da draw controle, ID instável, formato UV incorreto, stride/rebasing incorretos, construção BLAS ausente, IDs BLAS/TLAS sem correspondência com a instance, cobertura incompleta de frames, janela truncada, stdout duplicado/incompleto, estágio desconhecido, rejeição explícita do renderer e constantes válidas copiadas de um frame de origem errado. Os offsets explícitos são validados mantendo contadores do renderer e do observador independentes.

## Lifetime do staging

O adapter mantém um acquire inicial para a alocação intercalada de vértices e outro para os índices. `CommitGeometryToRT` cria um `StagingHold` somente para o marcador EDF. Seu destrutor libera ambos depois de `RtxContext::commitGeometryToRT`, inclusive no caminho de exceção. São dois releases mesmo quando as duas alocações pertencem ao mesmo `DxvkBuffer`, porque correspondem a dois acquires. Os futures já foram finalizados e o caminho GPU registra seus próprios holds antes dessa liberação. O comportamento genérico permanece inalterado.

## Comparação da imagem raster

`tools/edf_static_gpu_replay/compare_raster.py <baseline> <profile> --output <json>` compara `original_mrt0_755.bmp`, `original_mrt0_1182.bmp` e `original_mrt0_1292.bmp`. Ele valida o formato BMP 32-bit BI_RGB usado pelo replay, calcula SHA256 do arquivo e dos pixels BGRA/RGB normalizados para linhas de cima para baixo, e exige igualdade de dimensões, bytes e pixels nos três frames. Qualquer imagem RGB uniforme falha, pois uma imagem vazia não demonstra preservação de geometria visível. O JSON inclui contagem e bounding box dos pixels RGB diferentes do canto superior esquerdo; isso não pretende segmentar o objeto.

O relatório identifica `controlledReplay=true` e `syntheticTextures=true`. Fixtures com o arquivo `UNIT_TEST_FIXTURE_ONLY.txt` recebem `fixtureOnly=true`, `notGpuEvidence=true` e `evidenceKind=synthetic_verifier_fixture`. Os dez testes em `raster_comparator_unit_tests.json` validam igualdade e bounding box, mudanças RGB/alpha, imagens uniformes, orientação do arquivo, truncamento, frame ausente, formato e dimensões incorretas. São imagens geradas para testar o parser, sem execução GPU. Os BMP são readbacks anteriores ao Present; a verificação de logs continua necessária para confirmar o caminho scene-only e BLAS/TLAS.
