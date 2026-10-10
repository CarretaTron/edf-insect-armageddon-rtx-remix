# EDF: plano de composição restrita à mesh validada

Análise em 8 de outubro de 2026. Esta entrega altera somente documentação e ferramentas offline. O renderer e a instalação ativa do jogo não foram alterados nesta etapa. As opções e funções novas descritas abaixo são propostas, não comandos já disponíveis.

## Evidência disponível

A sessão real [session_1791490947848472_12776](<D:/EDF IA RTX REMIX/captures/edf-pt-probe/session_1791490947848472_12776>) contém três frames: 1043, 1112 e 1171. Os metadados registram uma draw atual, uma surface e uma instance permitida, 252 vértices, 432 índices/144 triângulos, câmera atual com erro zero entre as matrizes decodificadas e as matrizes usadas pelo renderer, resolução 1920×1080 e jitter zero. `gpuCompleted=true`, os quatro targets originais passam por comparação completa antes/depois, e `compositionPerformed=false`.

O [probe_verification.json](<D:/EDF IA RTX REMIX/captures/edf-pt-probe/session_1791490947848472_12776/probe_verification.json>) registra três amostras aprovadas, geometria CPU/GPU correspondente e radiância não nula. Isso prova PT offscreen para essa mesh e essas três câmeras. Não prova substituição visual, iluminação original recuperada ou PT contínuo em tempo real. O campo legado `evidenceKind=controlled_offscreen_runtime_replay` do verificador não distingue sozinho gameplay de replay; a origem desta sessão é o teste real informado pelo usuário e corroborado pelo executável e log da sessão.

Identidades que continuarão obrigatórias:

| Dado | Valor |
|---|---|
| Mesh | `925aa18481228b01392a574dbe28ba4e21aa1616` |
| VS | `f098f457d9ae603bea5f84ec985d473c29cfb0c9` |
| PS | `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8` |
| Geometria interna | `0x32aeb7bb7e772c13` |
| Trace | `0x4544465354415449` |
| Executável | `EDF-IA.exe`, SHA1 `d2e90641c560420d279a629ddd86e13cb767f464` |

O novo log também demonstra uma ausência normal da mesh: no frame 867, `current_frame_draw_count=0`, `latest_draw_frame=866`, `camera_last_update_frame=866` e `camera_valid=0`. No frame 910 há novamente uma draw e câmera atual válida. Não usaremos a câmera anterior para disfarçar essa ausência.

## Ponto de composição e limites dos buffers

O caminho confirmado é:

```text
DrawIndexedPrimitive da candidata
  → raster original em três MRTs fora da tela + D24S8
  → demais draws/passes do EDF
  → D3D9SwapChainEx::Present
  → D3D9Rtx::EndFrame(backbuffer)
  → fila CS: RtxContext::endFrame / injectRTX(present_fallback)
  → PT e pós-processamento em imagens próprias
  → EdfStaticProbe::queueReadback, sem blit
  → apresentação: cópia do backbuffer para imagem WSI
  → HUD do Runtime/ImGui → apresentação
```

O target de injeção é o backbuffer do jogo, preservado no **slot 0**. Os slots preservados **1–3** são os MRTs da candidata. Nas três amostras, `sourceTargetSessionId` é diferente de `injectionTargetSessionId`: não são a mesma imagem. O backbuffer alterna entre dois IDs de sessão. Um ID ou endereço não é identificação suficiente de conteúdo ou frame.

Os quatro targets capturados são `VK_FORMAT_B8G8R8A8_UNORM` (44), 1920×1080. O log da swapchain mostra formato WSI `VK_FORMAT_R8G8B8A8_UNORM`. Será necessário preservar swizzle BGRA/RGBA, os retângulos de apresentação, a resolução efetiva e eventual gamma ramp do jogo. O formato UNORM não prova se os números representam RGB linear ou gamma.

O decoder exige viewport integral, MRTs A8R8G8B8, ausência de MSAA, passe opaco e depth D24S8. Ele **não exporta os texels do depth original**. `EdfStaticProbe` só mantém referências aos três targets de cor. A comparação de integridade atual cobre quatro imagens de cor, não a superfície D24S8.

| Saída RTX | Formato real | Uso possível na composição |
|---|---|---|
| `linear_z` | R32_SFLOAT (100) | Máscara da mesh reconstruída e profundidade em view space. Hits têm Z negativo nestes frames; miss é `500001`. Não é o depth normalizado do EDF. |
| `albedo` | A2B10G10R10_UNORM_PACK32 (64) | RGB linear de refletância; não é cor final iluminada. Alpha de 2 bits não será usado como cobertura. |
| `direct_diffuse_radiance` | R16G16B16A16_SFLOAT (97) | Intermediário linear HDR demodulado. Não equivale sozinho à cor final da mesh. |
| `final_offscreen` | R16G16B16A16_SFLOAT (97) | Copiado **depois** de tonemapping, lens effects, conversão gamma/dither e debug view. Float16 descreve armazenamento, não garante conteúdo HDR. No caminho normal do código, gamma 2,2 aproximada é aplicada; os flags individuais que podem suprimir essa conversão não foram exportados nestas capturas. O modo `display` do visualizador é explícito e conserva os valores RGB, sem novo tonemapping/gamma. |

`dispatchSRGBDither` grava alpha=1 inclusive fora da mesh e satura os canais. Portanto **alpha de final_offscreen não é máscara de objeto**. A máscara deve usar `isfinite(z) && z != missLinearZ`, com identidade/frame conferidos. O renderer usa aproximação gamma 2,2 em `linearToGamma`; aplicar tonemapping ou gamma novamente à saída final altera o resultado. A opção `captureScreenImage` suprime essa conversão e debug view pode substituir a saída; uma futura captura deve registrar explicitamente esses estados.

O PS original escreve três saídas diferentes. O bytecode prova, por exemplo, `oC2.xyw = normal.xyzz * 0.5 + 0.5`, enquanto `oC2.z` recebe um termo especular. `oC0.xyz` recebe iluminação solar calculada pelo PS; `oC1.xyz` inclui amostragem de cubemaps, e `oC1.w` recebe o termo de fog vindo do VS. Ainda falta provar como os passes consumidores usam esses canais. Substituir um desses MRTs por `final_offscreen` destruiria sua semântica.

## Escolha do primeiro experimento visual

Recomendo um **preview de diagnóstico em retângulo limitado**, usando somente a candidata, antes da substituição sobre o objeto original. É uma etapa curta para provar apresentação, formato de cor, sincronia, isolamento e recuperação. O preview deve ser rotulado como tal: não demonstra oclusão correta nem replacement do objeto na cena.

| Alternativa | Evidência disponível | Decisão |
|---|---|---|
| Copiar o resultado RTX inteiro para o backbuffer | Contém somente uma mesh; restante da cena ausente do TLAS. | Rejeitada: apaga EDF, efeitos e HUD. |
| Colocar RTX diretamente nos pixels de `linear_z` | Máscara da mesh está validada, mas ignora oclusores do resto do EDF e HUD/efeitos. | Adiar: pode desenhar a mesh através de personagens, cenário, UI ou partículas. |
| Preview em ROI reservado | Não depende de semântica do depth global ou dos MRTs. Pode verificar pixels externos exatamente. | Primeiro experimento proposto. |
| Substituição alinhada com máscara de visibilidade original | Exige depth, cobertura, ordem e transformações dos passes consumidores comprovados. | Objetivo seguinte, após coleta específica. |

O preview deve ser gravado **somente na imagem WSI de apresentação**, no mesmo frame em que o resultado offscreen foi gerado. Backbuffer, três MRTs e depth do jogo não são escritos. Isso evita contaminar um eventual histórico do EDF que reutilize o backbuffer após Present; não sabemos se o jogo refaz todos os pixels no frame seguinte.

Esse modo altera intencionalmente os pixels apresentados dentro do ROI. Não prometer preservação de HUD naquela área: o HUD do EDF já pode estar incorporado no backbuffer, e HUD/ImGui do Runtime podem ter sido gravados na imagem WSI. O ROI precisa ser escolhido a partir da imagem original exportada; deve ser pequeno, configurável e validado visualmente. A imagem raster original continua intacta e o modo desarma após um frame. Desativar o modo ou pressionar F8 restaura a apresentação raster nos frames seguintes; não desfaz comandos GPU que já foram submetidos. Falha GPU/device-lost tem a recuperação normal do jogo.

Os PNGs offline em [images](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images>) permitem propor inicialmente o ROI `[x=1040, y=800, largura=820, altura=200]` em 1920×1080. Na imagem original da primeira amostra, ele ocupa a região de asfalto inferior direita, enquanto o HUD observado fica no topo e na lateral inferior esquerda. Um mockup offline desse painel ilustra tamanho e localização; não representa composição GPU executada nem comprova que o ROI estará livre de HUD/efeitos em outras situações. O ROI é somente uma proposta para revisão visual e precisará ser revalidado no frame do futuro teste.

Na primeira amostra, a máscara RTX abrange `x=1352..1916, y=562..616`, sobre regiões da imagem original que contêm ônibus, árvores e estruturas. A correspondência espacial ajuda a localizar a candidata, mas não demonstra visibilidade final exclusiva dela nem autorização para substituir todos esses pixels. Preservação de oclusão continua pendente das evidências de depth/cobertura descritas abaixo.

O hook concreto proposto é o callback da fila CS de `D3D9SwapChainEx::SubmitPresent`. A commandlist base, gravada no contexto dedicado `m_context`, já copia o backbuffer e desenha HUD/ImGui. Para o frame solicitado, submeter essa lista com o semaphore `acquire`, mas adiar o sinal de `present`. Em seguida, gravar/submeter uma segunda lista em um contexto próprio da composição, com barreiras entre a escrita anterior e a escrita/leitura do ROI, e sinalizar `present` somente depois dela. Só então chamar `presentImage`. O caminho padrão sem preview continua submetendo a lista original com os semaphores existentes.

Esse desenho exige validar ordenação na mesma graphics queue e os aspectos/layouts/ownership da imagem WSI. `PresentImage` chama `m_parent->Flush()` antes de preparar a apresentação; o resultado PT deve ser copiado para um recurso próprio e publicado com frame ID **na fila CS**, com referência mantida até a submissão da segunda lista. Nunca acessar no thread D3D9 um resultado futuro do RtxContext. `RtxContext::onPresent` não é um hook de overlay automaticamente seguro: a commandlist base é gravada em outro contexto. Se não houver preview atual pronto, sinalizar/apresentar somente o raster base.

Os contadores precisam ser distinguidos explicitamente: o argumento `FrameId` de `SubmitPresent` recebe o índice `i` do loop de SyncInterval, e não o frame RTX nem `m_frameId` da swapchain. O frame RTX deve ser conferido por `getCurrentFrameId()` na fila CS **antes** de `DxvkDevice::presentImage`, que incrementa `QueuePresentCount`. Registrar separadamente frame RTX, request, serial D3D9, índice do loop e imagem WSI. Inicialmente restringir o preview a uma apresentação por chamada, sem repetição de SyncInterval; os outros caminhos continuam raster. A segunda lista também precisa participar da conclusão de frame: o sinal `m_frameLatencySignal` hoje é gravado na lista base. Na tentativa com preview, adiar esse sinal para depois do overlay, mantendo o valor `m_frameId`, para que frame latency e lifetime de recursos não considerem o frame concluído antes da escrita final. Esse contrato deverá ser testado no replay antes de instalar qualquer composição.

Antes do overlay, copiar o ROI original da imagem WSI para scratch próprio, e registrar um readback completo pre/post para auditoria. A primeira implementação pode gerar uma textura preview própria RGBA8 com compute e usar blit/cópia **limitado ao retângulo de destino**, somente se usage/format features de transferência da imagem WSI forem suportados. Caso seja necessário um draw, exigir scissor/viewport restritos, depth desligado e load do conteúdo anterior, sem clear/discard da imagem. **Não reutilizar diretamente `DxvkSwapchainBlitter::draw` para o painel**: no caminho de retângulo parcial ele chama `clearRenderTarget`, o que poderia apagar conteúdo externo ao ROI. Recursos não suportados rejeitam o preview.

## Contrato proposto para o preview

Criar `EdfStaticComposition` separado do decoder, com `enabled=false`, `mode=PreviewRectangle`, frameCount=1, ROI explícito e rearm manual F9/F8. Preservar `rtx.edfStatic.rasterOnly=true`; sua opção antiga não autoriza composição global. Não haverá autorun no perfil de gameplay.

Condições antes de registrar qualquer escrita:

1. Executável e hashes exatos, uma draw reconhecida neste frame, uma instance atual visível e geometria original inalterada: 252 vértices/432 índices.
2. Câmera Main atual; matrizes dos shader constants iguais às comprovadas; zero jitter, sem free camera, upscaler, DLFG ou câmera gravada.
3. Uma tentativa de composição por contador `QueuePresentCount`, gerada somente no `present_fallback`, consumida pela apresentação correspondente. Nenhuma reexibição de saída de frame anterior quando a mesh desaparecer. Repetições de SyncInterval sem novo frame não autorizam nova geração do resultado.
4. Mesma resolução, viewport, retângulos de apresentação e formatos conhecidos; sem MSAA, camadas extras ou resize/Reset durante a solicitação. Mudanças desarmam o preview e descartam referências antigas.
5. Conteúdo da saída final e estado de tonemap/gamma/debug registrados; fundo determinado pela máscara `linear_z`, não pelo alpha. Recursos fonte somente para leitura.
6. ROI integralmente dentro da imagem WSI, área limitada, retângulos de apresentação mapeados e cópia original alocada antes da composição. Guard falho retorna ao raster original, sem registrar escrita. Gamma ramp desconhecida/não identidade deve ser registrada e inicialmente rejeitada, sem compensação adivinhada.

A imagem WSI apresentada deve manter os bytes originais fora do ROI. No ROI, usar crop da mesh com margem e preservação de aspecto, máscara de hits, fundo de diagnóstico e borda claramente identificável. Para o primeiro teste, evitar blend dependente de gamma: copiar o RGB final display-referred para a área do preview com conversão de formato/swizzle explícita. Se futuramente houver alpha blend, ambos os termos precisam ser convertidos ao mesmo espaço linear antes da mistura.

Não transformar o comprovante de readback em bloqueio síncrono da apresentação. O job de fence comprova a conclusão depois da submissão, mantendo `Rc` dos recursos; ele não publica imagem de frame passado para o jogo. Logs distinguem `composition_recorded` de `composition_gpu_completed`. Qualquer falha observada desabilita as próximas tentativas; não é prova de rollback de um frame já apresentado.

Arquivos previstos para esse próximo incremento, após revisar este plano:

| Arquivo | Mudança localizada |
|---|---|
| `runtime/src/dxvk/rtx_render/rtx_edf_static_composition.{h,cpp}` | Opções, guards, ROI, scratch e dispatch de preview. |
| `runtime/src/dxvk/shaders/rtx/pass/edf_static_composition/edf_static_composition.comp.slang` | Gerar textura preview própria; máscara válida por profundidade RTX, crop e fundo identificado. |
| `runtime/src/dxvk/rtx_render/rtx_context.cpp` | Publicar resultado próprio/frame-local apenas no bloco `offscreenProbe`, sem entrar no blit global. |
| `runtime/src/d3d9/d3d9_swapchain.cpp` | Hook isolado no callback CS de `SubmitPresent`: segunda lista somente no preview, sinal de present após overlay, nenhum acesso concorrente a saída futura. |
| `runtime/src/dxvk/rtx_render/rtx_edf_static_probe.{h,cpp}` | Registrar fonte/gamma/ROI e readbacks separados pre/post WSI; preservar verificações completas do backbuffer e MRTs. |
| `runtime/src/dxvk/meson.build` | Registrar classe nova. |
| `runtime/tools/edf_static_gpu_replay/` | Replay positivo/negativo, verificador de pixels fora do ROI, ausência/câmera/resize/duplicação. |
| `profiles/edf-composition-preview/` | Perfil separado; composição desativada por padrão e recuperação documentada. |

O decoder, os hashes, a classificação das outras draws e os subsistemas genéricos de SceneManager/InstanceManager/BLAS/TLAS permanecem iguais.

## Evidência que falta para substituir a mesh na sua posição original

| Informação faltante | Onde obtê-la e por que é necessária |
|---|---|
| Visibilidade frente ao resto do jogo | Copiar o D24S8 usado no passe opaco após as últimas draws relevantes e antes de clear/reuso. Identificar imagem/aspecto/formato real Vulkan, packing e ordenação de Z; comparar projeção da mesh com esse depth. Z RTX em view space não pode ser comparado diretamente aos 24 bits normalizados. |
| Cobertura exclusiva da candidata | Gerar máscara própria da mesh com sua câmera/layout e comparar com depth e MRTs originais. Não usar cor/material como ID: outras meshes podem compartilhar shader/textura. Validar bordas, backface, clipping e tolerância ligada à quantização do depth. |
| Passe consumidor dos MRTs e ordem de composição | Capturar um frame curto com snapshots de todas as draws, shaders, textures, RT/depth, viewport/scissor, clears/cópias e ordem até Present. Os filtros atuais por VS/PS conhecido excluem draws de composição/HUD. Usar snapshots sem dumps gigantescos de buffers fora da candidata. |
| Momento válido do depth | Relacionar binding, clear e reuso por identidade de sessão e frame. O evento Clear atual registra flags/cor, mas não Z, stencil nem os retângulos; acrescentar apenas esses campos quando começar a auditoria de depth. |
| UI, transparências e efeitos sobre a mesh | Identificar quais passes posteriores cobrem os pixels da candidata. Um depth opaco não protege HUD ou partículas sem Z-write. Escolher hook antes desses passes ou uma máscara que preserve sua contribuição comprovadamente. |
| Transformação da imagem | Provar se viewport, downsampling, filtro, distortion, fog, gamma, tonemap e apresentação mantêm o mesmo footprint. A projeção CPU/GPU atual valida a saída RTX, não todos os consumidores do EDF. |

O PS já revela parte da semântica dos três MRTs, mas os consumidores ainda precisam ser analisados. Se substituição em MRT exigir converter RTX para campos de iluminação/fog que o jogo compõe depois, implementar esse mapeamento só após provar a operação completa. A alternativa no backbuffer exige máscara de visibilidade final válida, não apenas o hitmask do TLAS contendo uma mesh.

## Sequência de validação e próximo teste

Nesta entrega, executar o visualizador offline sobre a sessão real e inspecionar PNGs de albedo, radiância, saída final, profundidade e imagem original. Não instalar DLL nova nem mudar o perfil ativo. As instruções executáveis do visualizador estão no README entregue junto à ferramenta.

Se a inspeção confirmar orientação e leitura de cor, o próximo incremento é o preview limitado descrito acima. Antes de instalar um futuro pacote:

1. Compilar e executar x64 direto e x86 via Bridge com composição desligada; exigir backbuffer e MRTs totalmente idênticos ao baseline.
2. Executar um frame de preview com mesh/câmera válidas. Exigir fence GPU, candidato único, máscaras/câmera aprovadas, backbuffer/MRTs inteiramente inalterados e zero diferenças da imagem WSI fora do ROI. Exportar original, scratch e apresentado. Verificar que o semaphore de present não pode ser sinalizado antes de terminar a segunda lista.
3. Rejeitar sem escrita mesh ausente, camera stale, geometry/hash diferente, duplicação no mesmo frame, ROI inválido, Reset/resize e formato inesperado. Recuperação F8 e modo desligado devem retornar ao raster original nos frames seguintes.
4. Só então entregar um pacote separado e instruções de instalação manual com backup. No EDF, escolher ROI longe do HUD, F9 por um frame, conferir visual/log, F8 para cancelar e fechar o jogo antes da recuperação de arquivos. O teste pode causar hitch de compilação/readback; não medir desempenho contínuo nessa rodada.

Critério de sucesso desse teste futuro: primeiro preview RTX aparece no jogo com cor e câmera correspondentes, somente durante a solicitação, sem alteração externa ao ROI apresentado nem aos buffers originais do EDF, e recupera o raster. Substituição correta da mesh na cena e PT contínuo permanecem milestones separados.

## Fontes locais conferidas

- [D3D9Rtx::prepareEdfStaticDraw: guards de passe, depth e preserve raster](<D:/EDF IA RTX REMIX/runtime/src/d3d9/d3d9_rtx.cpp:802>).
- [D3D9Rtx::EndFrame: flush e fila CS](<D:/EDF IA RTX REMIX/runtime/src/d3d9/d3d9_rtx.cpp:1522>).
- [D3D9SwapChainEx::Present: EndFrame com backbuffer](<D:/EDF IA RTX REMIX/runtime/src/d3d9/d3d9_swapchain.cpp:460>).
- [PresentImage: blitter, HUD e ImGui](<D:/EDF IA RTX REMIX/runtime/src/d3d9/d3d9_swapchain.cpp:1175>) e [SubmitPresent: commandlist do contexto dedicado](<D:/EDF IA RTX REMIX/runtime/src/d3d9/d3d9_swapchain.cpp:1275>).
- [RtxContext::commitGeometryToRT: referências aos três MRTs](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_context.cpp:1023>).
- [RtxContext::injectRTX: guards e offscreen](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_context.cpp:488>), [demodulation](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_context.cpp:742>), [tonemap/gamma](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_context.cpp:803>), [readback sem blit](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_context.cpp:840>).
- [EdfStaticProbe::beginFrame: whitelist/câmera/originals](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_edf_static_probe.cpp:525>) e [queueReadback: formatos e callback GPU](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_edf_static_probe.cpp:620>).
- [sRGB/dither: alpha=1 e saturação](<D:/EDF IA RTX REMIX/runtime/src/dxvk/shaders/rtx/pass/srgb_dither/srgb_dither.comp.slang:40>) e [gamma 2,2 aproximada](<D:/EDF IA RTX REMIX/runtime/src/dxvk/shaders/rtx/utility/color.slangh:89>).
- [Blitter da apresentação: estado do draw e clear do caminho parcial](<D:/EDF IA RTX REMIX/runtime/src/dxvk/dxvk_swapchain_blitter.cpp:110>).
- [PS original: saídas MRT](<D:/EDF IA RTX REMIX/analysis/edf/new_candidate/44b92a29eb3e5f2ee6d53f02e14fb11a01511da8.dxso.dis:29>) e [VS original](<D:/EDF IA RTX REMIX/analysis/edf/new_candidate/f098f457d9ae603bea5f84ec985d473c29cfb0c9.dxso.dis:29>).
- [Capture atual: filtros de shader](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_compat_capture.cpp:270>) e [Clear: campos atuais](<D:/EDF IA RTX REMIX/runtime/src/d3d9/d3d9_device.cpp:1621>).
