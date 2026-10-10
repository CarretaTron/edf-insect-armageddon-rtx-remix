# Visualizador offline das capturas EDF RTX

Converte os quatro readbacks de cada frame em PNGs, sem executar o jogo ou modificar o renderer. Requer Python 3.12+, NumPy e Pillow. O ambiente `.tools/build-python` do workspace já contém essas dependências.

## Executar

No PowerShell:

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
& '.\.tools\build-python\Scripts\python.exe' `
  '.\runtime\tools\edf_probe_viewer\view_capture.py' `
  '.\captures\edf-pt-probe\session_1791490947848472_12776' `
  --output '.\analysis\edf\offline_visualization\session_1791490947848472_12776\png' `
  --verification '.\captures\edf-pt-probe\session_1791490947848472_12776\probe_verification.json' `
  --final-encoding display `
  --exposure-ev 0 `
  --expected-samples 3
```

O diretório de saída precisa estar vazio ou não existir. Para executar novamente, escolha outro diretório de saída. Ele deve estar separado da árvore de captura, pois o visualizador recusa escrever sobre arquivos de origem.

Abra `comparison_final_offscreen.png` e `comparison_direct_diffuse_radiance.png` para comparar as três amostras. `comparison_albedo.png` e `comparison_linear_z.png` mostram a textura base e a profundidade. Os painéis usam o mesmo recorte de tela, exposição e escala de profundidade. `comparison_hit_mask.png` mostra exclusivamente os pixels em que o RTX encontrou geometria. `comparison_original_backbuffer.png` mostra a mesma região na imagem raster preservada, quando o slot 0 está disponível.

Cada frame também possui imagens completas no tamanho original e arquivos `_crop.png` com 16 pixels de margem ao redor dos hits. Os recortes não redimensionam nem alteram os pixels. A região visível dessa mesh pode ser pequena e estreita, por isso uma imagem completa pode parecer quase vazia.

## Formatos e cores

| Readback | Formato Vulkan | Interpretação |
|---|---|---|
| `linear_z` | 100, `R32_SFLOAT` | Float32 little endian, profundidade do espaço de câmera. |
| `albedo` | 64, `A2B10G10R10_UNORM_PACK32` | R nos bits 0–9, G 10–19, B 20–29, alfa 30–31. RGB linear UNORM do GBuffer Remix. |
| `direct_diffuse_radiance` | 97, `R16G16B16A16_SFLOAT` | RGBA float16 little endian; RGB é o sinal HDR linear intermediário de radiância difusa direta demodulada. |
| `final_offscreen` | 97, `R16G16B16A16_SFLOAT` | Float16 não determina a codificação de cor. O estágio final pode já ter tonemapping e transferência de saída. |

A radiância direta recebe exposição fixa `2^EV`, Reinhard `x/(1+x)` e a função sRGB por partes. Valores negativos são limitados a zero para exibição; a contagem permanece no manifesto. O albedo recebe somente a função sRGB por partes. Os dados de origem permanecem intactos.

A radiância intermediária demodulada não é, isoladamente, a contribuição final aplicada ao material. O albedo exibido é o GBuffer produzido pelo Remix a partir do material legado e suas regras de conversão; essa imagem não comprova identidade exata com a textura original do EDF nem valida o material completo.

Para a sessão EDF informada, `--final-encoding display` conserva os valores finais armazenados, limitando e quantizando para PNG de 8 bits. Não aplica outro tonemapping, exposição ou gamma. O caminho normal do Runtime escreve o resultado após tonemapping e conversão aproximada de gamma 2.2. Essa conversão não é a função sRGB exata. A instrumentação atual não registra todos os flags de transferência e debug por frame; o modo `display` expressa a interpretação auditada do caminho normal e não prova a transferência individual por meio dos metadados.

Os modos `linear` e `hdr` são explícitos para capturas cuja semântica seja conhecida: `linear` aplica somente sRGB; `hdr` aplica exposição, Reinhard e sRGB. `srgb` é uma indicação explícita de entrada já sRGB e conserva seus valores, como `display`, mas faz uma afirmação mais específica sobre a entrada. Não selecione esses modos apenas pelo formato float16.

O padrão `--final-encoding auto` recusa adivinhar. Ele exige `--encoding-proof arquivo.json`, com `schema: 1`, `finalEncoding`, `explanation`, `sources` e uma lista `metadataSha256` cobrindo todas as amostras atuais. A evidência completa é copiada para o manifesto, incluindo qualificações sobre inferência do caminho ou flags não registrados.

A máscara vem do `linear_z` finito diferente do `missLinearZ` float32 declarado. Alfa não determina cobertura. Com o bit 0 de `rendererCameraFlags` ativo, a câmera é RH e a distância positiva é `-z`; o visualizador verifica o sinal. A profundidade usa uma escala comum nas três amostras, azul próximo, verde intermediário e vermelho distante. Pixels de miss ficam pretos.

## Integridade

O visualizador verifica schema, conclusão GPU, frame da câmera, dimensão, formatos, número de bytes, hashes e consistência de máscara/contagem/bounding box. Na presença de `--verification`, recalcula o SHA256 dos readbacks contra o verificador aprovado, ligado ao SHA256 dos metadados e aos respectivos FNV. Sem esse cache, recalcula também o FNV de cada readback, com custo maior em Python.

As imagens raster opcionais do slot 0 verificam separadamente os checksums dos arquivos `before` e `after`, usando o mesmo vínculo SHA256/FNV dos metadados e do verificador. Também passam por comparação integral entre si; igualdade dos dois arquivos, sozinha, não comprova que eles mantiveram o conteúdo capturado.

Todos os arquivos da pasta de entrada são comparados por SHA256 antes e depois. `viewer_manifest.json` registra os hashes de origem e saída, transformações de cor, escala comum, valores inválidos/negativos, clamping e estatísticas de alfa. O processo recusa dados não finitos; não converte valores inválidos em uma imagem silenciosamente.

Esses PNGs permitem examinar geometria e iluminação produzidas pelo Runtime. Não executam composição, não validam oclusão com outras meshes e não demonstram path tracing em tempo real dentro do EDF.

## Testes

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
$env:TEMP = 'D:\EDF IA RTX REMIX\runtime\_build\test-temp'
$env:TMP = $env:TEMP
& '.\.tools\build-python\Scripts\python.exe' '.\runtime\tools\edf_probe_viewer\test_view_capture.py'
```

Os testes cobrem bytes/endianness/canais, funções de transferência, uso de alfa, sentinel e profundidade, escala compartilhada, checksums, falhas de integridade, caminhos escapando a captura e proteção contra sobrescrita. As fixtures sintéticas são testes do visualizador e não evidência de execução GPU.
