# EDF — composição WSI experimental da mesh validada

**Pacote experimental preparado para teste manual no EDF.** O Runtime corrigido passou **18 casos GPU nativos: 12 x64 e seis via Bridge x86**, além de **115 testes Python, 15 contratos estáticos e 34 assertions de deployment**. A synchronization validation confirmou a remoção do erro de lifetime do descriptor pool introduzido pela primeira candidata. Erros genéricos de renderpass/samplers/layout também presentes no baseline desligado continuam: **`globalVulkanValidationPassed=false`**. O painel em gameplay real do EDF ainda não foi comprovado.

Implementado um painel RTX limitado à imagem de apresentação WSI, produzido a partir do **frame atual**, com composição desligada por padrão. O decoder continua restrito à mesh de **252 vértices, 432 índices / 144 triângulos**, pelos hashes já comprovados. A instalação ativa do EDF não foi alterada nesta etapa.

Os replays nativos controlados da versão corrigida usam geometria, bytecodes SM3 e três conjuntos de câmera/constantes reais do EDF, com **texturas, padrão raster e ambiente de apresentação sintéticos**. Eles não são gameplay do EDF, screenshots do jogo ou comprovação de scanout físico. A observação do primeiro painel dentro do EDF permanece como próximo teste manual. A revisão candidata anterior tinha um defeito comprovado de lifetime GPU e seus resultados históricos não são usados para aprovar a versão entregue.

O experimento também não demonstra replacement com oclusão correta, recuperação da iluminação original ou path tracing contínuo em tempo real. As três capturas reais anteriores continuam demonstrando PT offscreen no EDF; a prova nova é a gravação GPU restrita da WSI no harness nativo.

O contrato técnico do renderer está documentado em [edf-static-composition.md](<D:/EDF IA RTX REMIX/runtime/documentation/edf-static-composition.md>), separado das instruções manuais de instalação/teste.

## Runtime e distribuição

| Item | Valor |
|---|---|
| Base oficial | `0e18e55c04309c08e6206e768bc72b24ac83f853` |
| Build | x64, `debugoptimized`; cliente Bridge x86 + servidor/Runtime x64 para EDF |
| Runtime SHA256 pós-fix testado | `b051fe14b0965c7a45d3a2370d8dc68fc7a18e6ae8ba43ef4a5c4e0d7d745f9e` |
| Runtime bytes pós-fix | `189184512` |
| Runtime SHA256 histórico, não instalar | `cc2264ad9192882c2c209b237b7c67fc77355d35632f64d634426f0d041184d5` |
| Runtime bytes históricos | `188852736` |
| Pacote EDF | [edf-diagnostics-x86](<D:/EDF IA RTX REMIX/packages/edf-composition-preview/edf-diagnostics-x86>) |
| Manifesto | [package-manifest.json](<D:/EDF IA RTX REMIX/packages/edf-composition-preview/edf-diagnostics-x86/package-manifest.json>) |
| Perfil | [rtx.conf](<D:/EDF IA RTX REMIX/profiles/edf-composition-preview/rtx.conf>) — `composition.enabled=False`, `autorun=False`, `frameCount=1` |

O instalador manual confere o fingerprint SHA1 `d2e90641c560420d279a629ddd86e13cb767f464` do EDF, os hashes/tamanhos de todos os arquivos do manifesto, arquitetura e caminhos antes de escrever no GameRoot. O guia fixa o SHA256 pós-fix acima e recusa um pacote diferente. A candidata histórica não deve ser instalada.

## Caminho do frame

```text
Draw validada de EDF
  → decoder existente / SceneManager / InstanceManager / BLAS / TLAS
  → EdfStaticProbe::beginFrame: uma draw atual, uma instance permitida, câmera e hashes
  → PT e pós-processamento existentes em recursos próprios
  → EdfStaticComposition::preparePreview na fila CS, somente present_fallback
      → redução GPU da máscara linear_z e validação de finitude
      → crop GPU com margem / resize nearest / fundo e borda ciano
      → preview RGBA8 próprio, identificado pelo frame e request
  → Probe: readbacks anteriores e preservação do backbuffer + três MRTs
  → Present: lista base copia o raster original e desenha HUD/ImGui na WSI
  → segunda lista: preview condicional somente dentro do ROI WSI
      → WSI antes, scratch do ROI, painel esperado e WSI depois
      → nova leitura dos quatro targets originais, sem escrever neles
  → sinal de Present e frame latency depois da segunda lista
  → callback de conclusão GPU / comparação e sidecar de composição
```

O painel padrão é `[x=1040,y=800,width=820,height=200]`, com resolução estritamente 1920×1080. O crop usa hits de `linear_z`, não alpha: `isfinite(z) && z != 500001`. A redução também conta RGB final não finito em hits. Zero hits ou qualquer dado inválido impede todas as stores na WSI por uma condição no shader. O sidecar correspondente não será aprovado.

Os cálculos de crop, fit e amostragem são inteiros determinísticos, com margem de oito pixels, borda ciano de dois pixels e preservação de aspecto. O verificador reconstrói a mesma transformação na CPU a partir dos buffers final/depth daquele frame, em vez de aceitar apenas que a cópia do painel GPU corresponde ao próprio painel.

`final_offscreen` é RGBA16F **depois** do tonemapping, gamma aproximada 2,2 e dither. Não recebe gamma/tonemapping novamente na composição. O modo exige conversão gamma habilitada, screenshot Remix desligado e debug view zero. A WSI aceita inicialmente apenas `VK_FORMAT_R8G8B8A8_UNORM` (37); BGRA, sRGB e outros formatos são rejeitados, evitando confundir cópia bruta com conversão de canais.

## Sincronização e isolamento

A geração do painel ocorre na fila CS depois dos guards offscreen existentes. Nenhuma leitura de resultado futuro é feita no thread D3D9. Preview, bounds e referências das evidências ficam retidos por `Rc` até o uso GPU/callback pertinente. Também é necessário reter **o próprio contexto do overlay**, dono dos descriptor pools, até a conclusão GPU; somente reter imagens/buffers era insuficiente. Essa correção foi compilada, repetida nos 18 casos nativos e auditada com synchronization validation no positivo pós-fix. O consumidor exige o mesmo `QueuePresentCount`, câmera atual e backbuffer que originaram o pedido; uma saída antiga não é reutilizada quando a mesh desaparece.

No callback CS de `SubmitPresent`, a lista base aguarda o semaphore `acquire` e **não** sinaliza `present`. A segunda lista é submetida na mesma graphics queue, com barreira `ALL_COMMANDS/MEMORY_WRITE → ALL_COMMANDS/MEMORY_READ|MEMORY_WRITE` antes dos acessos. Ela sinaliza `present` e frame latency somente depois da escrita final e dos readbacks. Guard rejeitado utiliza uma lista final sem overlay, mantendo os sinais de conclusão necessários ao raster.

A WSI recebe `TRANSFER_SRC` opcional somente para o perfil EDF opt-in e somente quando a capability da surface suporta esse usage. O wrapper espelha os usages efetivamente criados. A escrita storage ocorre em layout `GENERAL`; a imagem volta a `PRESENT_SRC_KHR` antes da apresentação. Unsupported usage/format, gamma ramp não identidade e mapeamento de Present parcial rejeitam o painel.

A implementação depende da heurística de auto-flush de memcpy já estar desativada em `DxvkContext::recordGPUMemCopy` nesta revisão. Esse comportamento genérico não foi alterado pelo experimento. Um teste de contrato detecta sua reativação: os helpers de readback não podem submeter parte do overlay antes da lista base. Uma revisão que reative a heurística precisará de proteção explícita no contexto experimental e novos testes GPU. Resize/recriação de swapchain invalida o recurso pendente e drena trabalho GPU pertinente antes de destruir imagens WSI; esse cuidado permanece mesmo se a opção for desligada depois de uma submissão.

Falhas de gravação do compositor descartam a lista experimental ainda não submetida e preservam uma apresentação raster com os sinais finais. A restauração do layout durante unwind é somente bookkeeping, `noexcept`, porque os comandos da lista descartada não foram executados. Device-lost, falhas de alocação gerais e erros do driver continuam exigindo a recuperação normal/reinício do jogo; o experimento não promete desfazer um frame GPU já submetido.

Nenhum MRT, backbuffer ou depth/stencil original é destino dos stores do compositor. A proteção contra alias considera referência e handle Vulkan. Os quatro targets de cor recebem comparação completa antes do probe e depois da escrita WSI. A preservação de depth/stencil é estrutural, pela ausência de acesso de escrita experimental; **não há checksum binário completo desse depth neste incremento**.

## Evidência de validação disponível

Os resultados da matriz nativa abaixo pertencem ao Runtime pós-fix `b051fe14…`. O [aggregate final](<D:/EDF IA RTX REMIX/analysis/edf/composition/validation.json>) consolida os relatórios e as limitações. O campo `notEdfGameplay` dos replays permanece verdadeiro, mesmo quando `gpuCompleted` e `passed` são verdadeiros.

| Grupo | Evidência e estado |
|---|---|
| Python / source contracts | 115 testes Python e [source_audit.json](<D:/EDF IA RTX REMIX/analysis/edf/composition/source_audit.json>): 15 contratos de código aprovados. São 130 verificações CPU/estáticas, não substituem execução GPU. |
| Deployment | [deployment_verification.json](<D:/EDF IA RTX REMIX/runtime/_build/edf_composition_deployment_tests/6afca7f7609c4de0a4199cae9c5d9e46/deployment_verification.json>): 34 assertions aprovadas em arquivos artificiais no workspace. |
| Comandos manuais | [manual_commands_syntax.json](<D:/EDF IA RTX REMIX/analysis/edf/composition/manual_commands_syntax.json>): seis blocos PowerShell parseados sem erros; seus comandos não foram executados na instalação ativa. |
| x64 pós-fix | [matrix_verification.json](<D:/EDF IA RTX REMIX/runtime/_build/edf-composition-retained-x64/matrix_verification.json>): 12/12 casos aprovados — três câmeras, desativado, ROI inválido, ausência inicial/recuperação, geometria alterada/recuperação, duplicação inicial/recuperação, ausência posterior, Reset posterior, gamma não identidade e Present parcial. |
| Bridge x86 pós-fix | [matrix_verification.json](<D:/EDF IA RTX REMIX/runtime/_build/edf-composition-retained-x86/matrix_verification.json>): 6/6 casos aprovados — três câmeras, desativado, ausência inicial/recuperação e Reset posterior. |
| Raster baseline | [x64](<D:/EDF IA RTX REMIX/analysis/edf/composition/raster_comparison_retained_x64.json>) e [x86](<D:/EDF IA RTX REMIX/analysis/edf/composition/raster_comparison_retained_x86.json>): BMPs dos três frames fonte idênticos ao baseline desligado, com padrões raster/texturas sintéticos. |
| Vulkan synchronization validation | Positivo, Reset e baseline desligado pós-fix concluídos. O erro `VUID-vkQueueSubmit-pCommandBuffers-00070`/código `-1360208885` ligado à destruição dos descriptors está ausente. Não foram observados códigos de erro ou assinaturas de hazards novos em relação ao baseline nos dois casos habilitados. Os três casos continuam reprovados globalmente por seis grupos de erros e quatro ocorrências de hazards também presentes com composição desligada; os erros não foram suprimidos. **Sem aprovação global Vulkan.** |
| EDF real com painel | Pendente de execução manual com este pacote. |

As três câmeras estão representadas pelas linhas `positive_camera0/1/2`; os números desses nomes não são os frames reais das capturas anteriores. Nos positivos, a correspondência CPU/GPU e os bytes WSI foram verificados, com `outsideChangedPixels=0`, painel esperado correspondente e quatro targets originais inalterados. A comparação independente CPU do painel admite no máximo um nível de quantização RGBA8; a cópia painel GPU → WSI e os pixels externos são comparados exatamente. Os [PNGs e suas legendas](<D:/EDF IA RTX REMIX/analysis/edf/composition/images/README.md>) mostram as três câmeras e uma WSI sintética completa, inspecionadas para orientação/cor/localização do painel. Não são imagens originais do EDF.

Os hazards remanescentes são uma escrita posterior no scratch de BLAS, duas transições após clear em `shadow terminator fix` e uma transição após cópia em `final output`/Motion Blur. A ausência de novos códigos/assinaturas na comparação limitada não prova que toda sincronização GPU esteja correta. Essas falhas genéricas continuam um limite real do Runtime neste harness e não foram corrigidas ampliando o escopo deste experimento.

O teste do instalador comprova journal independente, restauração do estado imediatamente anterior, preservação byte a byte do journal legado, retomada de Restore, variantes controladas ON/OFF inclusive CRLF e recusa de executável/pacote/backup/journal corrompidos ou arquivos editados pelo usuário antes de sobrescrevê-los. A sandbox do Windows recusou `File.Replace`; a execução final dessa suíte foi feita sem sandbox **somente nas fixtures** do workspace.

## Evidência por request e falhas globais

O JSON do probe `edf_pt_request_<request>_frame_<frame>.json` continua declarando `compositionPerformed=false`, pois descreve a fase offscreen. O sidecar `..._composition.json` descreve a WSI e registra conclusão GPU, máscara válida, frame/request, crop/fit, ROI, transfer/debug flags, formato, checksums e contagens de diferenças. Não alterar o JSON offscreen para torná-lo uma prova de composição.

O verificador exige associação positiva entre os dois arquivos, hashes da única candidata, matrizes/câmera aprovadas, Runtime exato informado na execução, bytes fora do ROI iguais, scratch original correto e fidelidade do painel. `composition_recorded` comprova comandos registrados; `composition_gpu_completed` vem do callback após conclusão GPU. `presentation_queued` registra que o Present correspondente foi enfileirado com o sinal posterior ao overlay. Nenhum desses campos comprova sozinho scanout físico; `presentationDisplayVerified=false` permanece explícito.

A auditoria lê o **log inteiro**, não apenas os eventos do request aprovado. Eventos de falha do probe/compositor e padrões de erro como `SYNC-HAZARD-*`, `VUID-*`, `VK_ERROR_DEVICE_LOST`, erros de submissão/fence, SPIR-V ou compilação fazem o verificador recusar a sessão, mesmo se uma amostra isolada tiver `passed=true`. Logs de rejeição normal de câmera ausente, duplicação de injeção, gamma, ROI ou Present incompatíveis devem ser distinguidos de falhas de validação/submissão/corrupção.

## Próximo teste real e recuperação

Os **blocos PowerShell completos**, em ordem de execução, estão em [TESTAR.md](<D:/EDF IA RTX REMIX/profiles/edf-composition-preview/TESTAR.md>). Eles incluem:

1. Install com perfil OFF e Verify do pacote/estado;
2. Enable somente com jogo/Bridge fechados, reinício e F9 por um frame compatível;
3. acompanhamento dos logs, fechamento normal e seleção inequívoca da sessão;
4. `verify_probe.py` e `verify_composition.py` sobre a mesma sessão/log, exportando PNGs fora das capturas originais;
5. Disable para manter o pacote sem painel, ou Restore para retornar exatamente ao Runtime/perfil anterior.

Enable é autorização para armar manualmente com F9, não autorun. F8 cancela trabalho pendente e não desfaz submissões. Uma falha observada na auditoria GPU desativa próximas tentativas até reiniciar o processo. Durante o frame solicitado, o ROI pode cobrir HUD/efeitos naquela região; a finalidade é provar apresentação/isolamento/cor, sem depender de depth/oclusão ainda não recuperados.

O namespace novo `.edf-composition-backup` preserva o pacote atualmente instalado e não invoca o Restore legado `.edf-remix-backup`. Não usar o instalador antigo enquanto o experimento novo estiver ativo. Enable/Disable usam exclusivamente variantes de perfil autorizadas por SHA256; alterações manuais desconhecidas são preservadas e fazem Restore recusar, exigindo análise do journal.

Não instalar a revisão histórica para reproduzir o bloqueio. O próximo experimento usa somente o Runtime corrigido, com SHA256 fixado no guia e retestes vinculados ao aggregate.

Critério do próximo teste: observar o painel **dentro do EDF**, correlacionar seu frame/request com os eventos de conclusão, verificar CPU/GPU e integridade de todos os buffers cobertos, confirmar raster nos frames seguintes e recuperar o estado anterior. Sem essa execução real, não declarar a composição dentro do jogo aprovada.
