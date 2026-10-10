# EDF: visualização das três capturas GPU reais

Validado em 8 de outubro de 2026. O visualizador está implementado e testado; o renderer, os perfis e a instalação ativa do EDF não foram alterados nesta etapa. As capturas originais permanecem intactas. A composição visual foi investigada e documentada, sem implementação GPU.

## Resultado demonstrado

A sessão [session_1791490947848472_12776](<D:/EDF IA RTX REMIX/captures/edf-pt-probe/session_1791490947848472_12776>) corresponde ao gameplay real informado pelo usuário. O log identifica `EDF-IA.exe`; seu SHA256 é `5bb9098241bf185ef13d1f75e34115db67a983939b2b5f2c61ccf0115b676f89`. O Runtime instalado, medido somente por leitura após a execução, possui SHA256 `93b265b00cb55b51b25c5b7f0495d72a9e0cf76d55abe1462f932501843b4641`, igual ao pacote entregue anteriormente. Essa medição não é um fingerprint registrado dentro do processo no momento do frame.

| Request / frame | Hits RTX | Pixels com radiância direta não nula | IoU da silhueta CPU/GPU | Fence GPU | Quatro targets de cor |
|---|---:|---:|---:|---|---|
| 1 / 1043 | 8705 | 8703 | 100% | concluído | idênticos antes/depois |
| 2 / 1112 | 8602 | 8596 | 99,8954825% | concluído | idênticos antes/depois |
| 3 / 1171 | 7581 | 7577 | 100% | concluído | idênticos antes/depois |

A mesh continua restrita a 252 vértices e 432 índices/144 triângulos, hash `925aa18481228b01392a574dbe28ba4e21aa1616`. Há uma surface e uma instance permitida em cada experimento. As três câmeras são atuais; as matrizes de View e Projection usadas pelo renderer têm erro zero contra os valores decodificados registrados. Resolução: 1920×1080; jitter: zero. A diferença de nove pixels na segunda comparação está dentro da tolerância já existente de borda de um pixel; não relaxamos o verificador.

O [verificador executado novamente](<D:/EDF IA RTX REMIX/analysis/edf/visualization/actual_edf_verification.json>) passou com três amostras e `errors=[]`. Para essa repetição, o log ativo foi lido somente depois de conferir que seu SHA256 é igual ao log arquivado na sessão. Isso permite ao verificador legado localizar o renderer na instalação, sem escrever ali. Seu campo `evidenceKind=controlled_offscreen_runtime_replay` é genérico e não distingue sozinho replay de gameplay; o [relatório desta sessão](<D:/EDF IA RTX REMIX/analysis/edf/visualization/SESSION_VALIDATION.json>) registra separadamente a origem real e a proveniência.

O experimento comprova execução GPU e path tracing **offscreen** da candidata sob luz de diagnóstico. `compositionPerformed=false`. Não comprova substituição visual alinhada, iluminação original recuperada, oclusão pelo restante do jogo ou funcionamento contínuo em tempo real. A preservação binária cobre o backbuffer e três MRTs; não há readback antes/depois do depth/stencil D24S8 original.

## Formatos realmente capturados

Os formatos foram conferidos nos metadados, em `EdfStaticProbe::queueReadback` e nos shaders que escrevem os recursos. Os buffers são tightly packed, little endian, com linha superior primeiro; não possuem cabeçalho nem padding entre linhas. O visualizador usa os nomes completos registrados em cada JSON, incluindo prefixo request/frame.

| Buffer | Vulkan / ID | Bytes por pixel | Interpretação e exibição |
|---|---|---:|---|
| `final_offscreen` | R16G16B16A16_SFLOAT / 97 | 8 | RGBA float16. Estágio final após processamento; modo `display` conserva os canais armazenados, sem nova exposição, gamma ou tonemapping. |
| `direct_diffuse_radiance` | R16G16B16A16_SFLOAT / 97 | 8 | RGB linear HDR **intermediário demodulado**. Exposição fixa EV=0, Reinhard e transferência sRGB por partes para PNG. Alpha é auxiliar, não cobertura. |
| `albedo` | A2B10G10R10_UNORM_PACK32 / 64 | 4 | RGB linear do G-buffer do material Remix: R bits 0–9, G 10–19, B 20–29; alpha bits 30–31. Somente transferência sRGB para PNG. |
| `linear_z` | R32_SFLOAT / 100 | 4 | Float32 de Z em espaço de câmera. Nesta câmera RH, hits negativos e distância positiva `-z`; miss exato float32 `500001`. Escala comum de profundidade nas três imagens. |

Para 1920×1080, albedo e profundidade têm 8.294.400 bytes cada; os dois buffers RGBA16F têm 16.588.800 bytes cada. Esses tamanhos conferem nas três amostras. Todos os valores decodificados são finitos; não há RGB negativo.

O RGB da radiância ultrapassa 1: máximos globais de 1,762695 / 1,755859 / 1,762695 e 5373 / 5347 / 4709 valores de canais acima de 1. Ela precisa de tonemapping para exibição em 8 bits. A exposição e a curva permanecem iguais entre frames; não há normalização automática por captura. O albedo mostrado é um intermediário do material do Remix, não uma prova de identidade com o texel bruto ou de recuperação completa do material EDF.

O RGB da saída final está entre 0 e aproximadamente 0,919922; alpha é sempre 1. O fato de seu armazenamento ser float16 **não significa que ela ainda contenha radiância HDR**. Em [injectRTX](<D:/EDF IA RTX REMIX/runtime/src/dxvk/rtx_render/rtx_context.cpp:803>), o readback ocorre depois de tonemapping, conversão/dither e debug view. O [shader de saída](<D:/EDF IA RTX REMIX/runtime/src/dxvk/shaders/rtx/pass/srgb_dither/srgb_dither.comp.slang:46>) satura RGB e escreve alpha=1. A [transferência normal](<D:/EDF IA RTX REMIX/runtime/src/dxvk/shaders/rtx/utility/color.slangh:89>) aproxima gamma por potência 1/2,2, em vez da função sRGB exata.

Há uma limitação explícita: os metadados atuais não exportam os flags individuais de screenshot/conversão e debug view. Portanto, a transferência final é uma interpretação auditada do caminho normal, não uma medição desses flags por frame. O modo explícito `display` conserva os números RGB armazenados, sem aplicar uma segunda transformação. `auto` recusa adivinhar; outros modos só devem ser usados com semântica conhecida. Os antigos PPMs foram preservados, mas o novo visualizador lê os BINs, pois o preview legado aplica a mesma curva HDR inclusive à saída final.

Alpha da radiância chega a 65504 e alpha final vale 1 fora da mesh. Nenhum deles será usado como máscara. A máscara vem exclusivamente de `linear_z != float32(missLinearZ)`, após validar finitude, handedness, contagem e bounding box. A profundidade possui escala comum de 965,719360 a 1332,401978 unidades do espaço validado: azul próximo, verde intermediário, vermelho distante. Não interpretamos essas unidades como metros do motor.

## Imagens e validação do visualizador

Foram gerados **42 PNGs**: seis imagens completas e seis recortes por frame, mais seis comparações entre os três frames. As seis imagens são os quatro readbacks, máscara e backbuffer raster original. Os recortes individuais têm margem de 16 pixels; comparações usam o mesmo retângulo de tela `[1127,546,1920,703)` para preservar o deslocamento entre câmeras. A pequena área da mesh explica por que o PNG completo parece quase vazio.

- [Saída final das três capturas](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/comparison_final_offscreen.png>).
- [Radiância direta intermediária](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/comparison_direct_diffuse_radiance.png>).
- [Albedo do material Remix](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/comparison_albedo.png>).
- [Profundidade com escala comum](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/comparison_linear_z.png>).
- [Raster original na mesma região](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/comparison_original_backbuffer.png>).
- [Manifesto com formatos, estatísticas, transformações e hashes](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/viewer_manifest.json>).

![Recorte da saída RTX real do frame 1043, sem composição no jogo](<D:/EDF IA RTX REMIX/analysis/edf/visualization/images/request_1_frame_1043_final_offscreen_crop.png>)

A inspeção mostra componentes texturizados coerentes, iluminação de diagnóstico e profundidade distinta. Na terceira captura, a projeção se desloca para esquerda e para baixo. Isso concorda com as matrizes e a referência geométrica independente; não atribuímos identidades de entidades do jogo aos componentes desconectados.

**40 testes automatizados passaram**: formatos/endianness, ordem de canais, transferência por partes, Reinhard, exposição, final sem dupla conversão, alpha/sentinel, escala comum, dimensão e truncamento, checksums, câmera e isolamento de caminhos/escrita. Uma revisão independente conferiu os pixels de 18 imagens completas com fórmulas separadas, sem diferenças. Os 42 hashes PNG também correspondem entre a geração inicial e a final; a correção de integridade não altera os pixels das capturas válidas.

A revisão encontrou uma lacuna: comparar somente `before==after` do raster permitiria aceitar dois arquivos adulterados igualmente. Corrigimos isso antes da entrega. O slot 0 agora valida cada arquivo contra SHA256/FNV vinculados aos metadados, ou recalcula FNV sem cache, e depois faz a comparação integral. Os testes incluem corrupção idêntica dos dois arquivos, com e sem cache. [Código](<D:/EDF IA RTX REMIX/runtime/tools/edf_probe_viewer/view_capture.py>), [testes](<D:/EDF IA RTX REMIX/runtime/tools/edf_probe_viewer/test_view_capture.py>) e [log dos testes](<D:/EDF IA RTX REMIX/runtime/_build/edf_probe_viewer_unit_tests.log>).

Os **50 arquivos originais, 404.467.728 bytes**, conservam tamanho e SHA256 antes/depois. Os manifestos [anterior](<D:/EDF IA RTX REMIX/analysis/edf/visualization/original_inputs_before.json>) e [posterior](<D:/EDF IA RTX REMIX/analysis/edf/visualization/original_inputs_after.json>) registram a comparação. Nenhum arquivo da sessão foi substituído. Não foi necessário recompilar o Runtime: só ferramentas Python e documentação foram alteradas.

## Próximo experimento concreto

O [plano técnico](<D:/EDF IA RTX REMIX/analysis/edf/visualization/COMPOSITION_PLAN.md>) propõe primeiro um painel limitado **somente na imagem WSI apresentada**, depois da cópia raster e antes de sinalizar o semaphore de apresentação. O backbuffer e MRTs do jogo continuam intocados. Modo desligado por padrão, pedido manual de um frame, hashes/câmera/frame conferidos, recurso próprio e nenhuma reutilização de um resultado antigo.

Foi gerado um [mockup offline](<D:/EDF IA RTX REMIX/analysis/edf/visualization/mockup/request_1_frame_1043_offline_preview_mockup.png>) com ROI `[1040,800,820,200]` na resolução atual. Os três mockups têm **zero pixels diferentes fora do ROI**, conforme [verificação](<D:/EDF IA RTX REMIX/analysis/edf/visualization/mockup/mockup_verification.json>). Eles ilustram o layout usando PNGs existentes; não são screenshots de composição GPU no EDF e não comprovam sincronização ou recuperação no jogo.

Os riscos concretos do próximo incremento são sincronização entre os contextos RTX/apresentação, semaphore/layout da WSI, swizzle/gamma, retângulos de apresentação e HUD dentro do ROI. O blitter existente limpa o target no caminho parcial: reutilizá-lo diretamente pode apagar o restante da imagem. O plano define um caminho separado e verificação binária fora do ROI.

Substituição alinhada sobre o objeto ainda exige depth original, cobertura/oclusão e ordem dos passes de fog, UI, partículas e consumidores MRT. O hitmask de um TLAS com uma mesh não protege contra os outros objetos. A imagem raster dessas capturas contém ônibus e árvores na região da máscara RTX; colá-la diretamente não tem visibilidade final comprovada.

[validation.json](<D:/EDF IA RTX REMIX/analysis/edf/next_validation/validation.json>) foi atualizado: execução GPU real e visualização offline aprovadas; composição e path tracing contínuo permanecem pendentes. As [instruções exatas](<D:/EDF IA RTX REMIX/analysis/edf/visualization/TESTAR.md>) executam somente o teste offline disponível nesta entrega. As opções de composição do plano ainda não existem no pacote ativo.
