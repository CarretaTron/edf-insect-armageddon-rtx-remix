# EDF — incremento de instrumentação

Base oficial: `0e18e55c04309c08e6206e768bc72b24ac83f853`, branch local `edf-instrumentation`.

## Problema e solução deste incremento

O Runtime não interpreta automaticamente as matrizes e os materiais de um jogo SM3. Antes de adicionar regras, precisamos preservar o estado efetivo e os bytes que produziram cada draw. O observador coleta esses dados no device D3D9 x64, antes dos filtros RTX, inclusive com ray tracing desligado. Ele não altera matrizes, shaders ou a decisão de renderização.

Arquivos modificados:

- `src/dxvk/rtx_render/rtx_compat_capture.{h,cpp}`: configuração, seleção de frames/draws, snapshots e identidade de recursos.
- `src/util/util_compat_capture.{h,cpp}`: escrita assíncrona, limite de fila e orçamento, deduplicação SHA1, registro terminal de integridade.
- `src/d3d9/d3d9_device.{h,cpp}`: hooks de setters, quatro draws e Lock/Unlock dos buffers.
- `src/d3d9/d3d9_rtx.cpp`: fronteira de frame e algumas razões de classificação RTX.
- `src/d3d9/d3d9_common_buffer.{h,cpp}` e `d3d9_common_texture.cpp`: invalidação de IDs quando recursos são destruídos. Assim, reutilizar um endereço não reutiliza a identidade da sessão.
- `src/d3d9/d3d9_stateblock.cpp`: marcadores Capture/Apply; os setters gravados em stateblock são distinguidos dos aplicados.
- `src/util/meson.build` e `src/dxvk/meson.build`: registro dos novos fontes.
- `scripts-common/compile_shaders.py`: argumentos como lista, corrigindo o erro observado com espaços no caminho; `RTX_SHADER_BUILD_JOBS` limita a memória de compilação.
- `tools/edf_capture/`: análise offline, fixture de schema, testes e configurações de diagnóstico.
- `tools/edf_capture_writer_tests/` e `tools/edf_d3d9_probe/`: testes do gravador e aplicativo D3D9 controlado.
- `bridge/src/client/d3d9_device.cpp` e `bridge/src/server/main.cpp`: correções D3D9 comprovadas pelo probe: efeitos UP no cache, payload UP com MinVertexIndex não zero e envio/ordem de MultiplyTransform. Detalhes em [TEST_REPORT.md](TEST_REPORT.md).

## Conteúdo de uma captura

`records.jsonl` contém eventos, shaders por conteúdo, estados deduplicados, draws referenciando `stateId`, resultados RTX e estado terminal. `blobs/<sha1>.bin` contém bytecode DXSO, disassembly, constantes completas F/I/B, transforms e intervalos VB/IB usados. UP é copiado durante a chamada, sem guardar ponteiros temporários.

IDs de recurso são locais à sessão e têm ciclo de vida. Versões VB/IB avançam ao terminar um lock de escrita; o hash do blob identifica os bytes efetivamente copiados. DISCARD/NOOVERWRITE são registrados. Índices permanecem originais, com `BaseVertexIndex` e offsets; o analisador não confunde o blob parcial com um buffer começando no byte zero.

Os bancos de constantes vêm do estado efetivo, incluindo valores enviados antes da janela. `def` do bytecode é documentado separadamente e aplicado pelo analisador. Bool usa bits compactados de 32 bits. Matrizes `Matrix4` do Runtime usam armazenamento por colunas; a orientação/semântica das constantes VS ainda precisa ser demonstrada pelo shader.

Slots de textura/sampler são índices internos do Runtime: PS 0–15, displacement 16, VS 17–20. RT/depth incluem mip/face e dimensões da superfície. A captura inicial registra metadados de texturas; readback dos pixels GPU ainda não foi implementado. Buffers GPU-written, indisponíveis, bloqueados ou fora dos limites são explicitamente marcados indisponíveis. Streams com `INSTANCEDATA` não recebem interpretação inventada.

`capture_status` confirma drenagem da fila e informa erro/truncamento. `available: true` no blob significa que foi aceito na fila; verifique também status terminal, tamanho e hash. Uma captura sem status terminal não é prova íntegra. As razões RTX atualmente registradas são um subconjunto; `prepareFlags` está disponível para toda draw selecionada.

## Compilar

No checkout, com MSVC e Vulkan SDK instalados:

```powershell
$env:PATH = 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts;' + $env:PATH
$env:VULKAN_SDK = 'C:\VulkanSDK\1.4.363.0'
$env:RTX_SHADER_BUILD_JOBS = '2'
meson compile -C _CompDebugOptimized_x64 -j 2
```

Depois de mudanças em `meson.build`, reconfigure explicitamente se o primeiro setup estava em andamento durante as edições. Use `meson compile`, conforme o skill do repositório.

Para o Bridge, inicialize MSVC na arquitetura do build antes de `meson compile`; a ativação automática do Meson pode selecionar x64 para o diretório x86. Os builds estão em `bridge/_CompDebugOptimized_x86` e `bridge/_CompDebugOptimized_x64`. Depois de instalar o Runtime no diretório `_output` com `meson install -C _CompDebugOptimized_x64 --tags output --no-rebuild`, `tools/edf_capture/package.ps1` monta bundles x64 e x86 em uma pasta nova, excluindo arquivos PDB/LIB/EXP/OBJ e registrando SHA256 de cada arquivo. Use `-PackageRoot` diferente para preservar pacotes existentes.

Os binários finais passaram no probe x64 e no caminho Bridge x86, com três frames, 24 draws e 1.584 verificações em cada execução. Os 43 testes unitários do analisador/verificador passaram. A captura desativada e o filtro de executável também foram testados. Isso valida a instrumentação, sem provar qualquer milestone dentro do EDF. Evidências e limitações: [TEST_REPORT.md](TEST_REPORT.md).

## Capturar no EDF

Use Bridge x86 e servidor x64 compilados desta mesma revisão junto ao Runtime x64. O arquivo `edf-observe.conf` mantém ray tracing desligado e permite o observador somente para `EDF-IA.exe`.

Instale o pacote quando for testar, com o jogo fechado. O script mantém backup e journal de hashes e copia o perfil como `rtx.conf`, permitindo iniciar pela Steam sem depender de variáveis herdadas:

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
.\tools\edf_capture\deploy.ps1 -Action Install -GameRoot 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
```

Inicie pela Steam e entre em uma missão. Pressione F10 por cerca de meio segundo e solte: o próximo frame recebe a captura. Faça duas capturas com câmera parada e uma terceira após mover a câmera. Cada acionamento gera uma pasta nova em `D:\EDF IA RTX REMIX\captures\edf`. `frameCount` aceita 1–3; `startFrame` permite uma janela automática por contador zero-based de fronteiras `D3D9Rtx::EndFrame` com `callInjectRtx=true`. Callbacks auxiliares de ativação/redimensionamento com `false` não contam. Múltiplos swapchains podem criar mais de uma fronteira por frame percebido pelo jogador.

O appid `23530` foi conferido no manifesto Steam local. Para iniciar posteriormente por comando:

```powershell
Start-Process 'steam://rungameid/23530'
```

Feche o jogo antes de restaurar:

```powershell
.\tools\edf_capture\deploy.ps1 -Action Restore -GameRoot 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
```

Detalhes da instalação e recuperação: [README-deploy.md](README-deploy.md). O observador está desligado por padrão no Runtime e exige que o nome do executável corresponda ao perfil. O arquivo `edf-profile.json` registra o fingerprint observado do EDF, mas ainda não ativa um decoder por fingerprint.

Filtros opcionais: `firstDraw`, `lastDraw`, `vertexShaderHash`, `pixelShaderHash`, `renderTargetHash`. Hash de shader é SHA1 hexadecimal do bytecode original. Hash de RT é o hash do descritor, não dos pixels. Eventos globais continuam sujeitos ao orçamento; dumps de buffers respeitam draws selecionadas.

```powershell
& 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe' tools/edf_capture/analyze_capture.py 'D:\EDF IA RTX REMIX\captures\edf\PASTA\records.jsonl' --output 'D:\EDF IA RTX REMIX\captures\edf\PASTA\inspection'
```

O manifesto mantém categoria `unknown`. OBJ exporta `POSITION0` bruto em espaço desconhecido; isso ainda não é reconstrução RTX validada. Matrizes candidatas recebem testes matemáticos, sem serem chamadas de World/View/Projection só por aparência.

## Evidência exigida antes do decoder

Escolher uma draw estática opaca e comprovar a cadeia de posição/UV do VS. Validar índices/layout, separar câmera de objeto, comparar a projeção com o raster e repetir em outra posição de câmera. WVP isolada não separa W/V/P unicamente. Só depois inserir regra por hash+layout+passe e fornecer transforms ao caminho existente de vertex capture.

O perfil JSON está vazio deliberadamente: ainda não contém interpretação do EDF. M3–M10 exigem capturas reais e validação incremental, incluindo pixels de textura e iluminação controlada.
