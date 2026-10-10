# Validação do incremento de diagnóstico

Base oficial: `0e18e55c04309c08e6206e768bc72b24ac83f853`. Runtime x64 e Bridge x86/x64 compilados em `debugoptimized`, MSVC 14.44, com mudanças locais de instrumentação. O jogo instalado não foi alterado nem iniciado.

## Resultado

| Verificação | Resultado | Evidência local, relativa ao checkout |
|---|---|---|
| Runtime x64 compilado | Passou | `build-instrumentation-stateblock-cache-final.log` |
| Bridge cliente x86 compilado | Passou | `bridge/build-client-stateblock-final.log` |
| Bridge servidor x64 compilado | Passou | `bridge/build-server-transform-fix.log` |
| Probe final x64 | Exit 0, 3 frames, 24 draws, 1.584 checks | `_build/edf_d3d9_probe/instrumented_x64/verification-final.json` |
| Probe final x86 via Bridge | Exit 0, 3 frames, 24 draws, 1.584 checks | `_build/edf_d3d9_probe/instrumented_x86/verification-final.json` |
| Analisador sobre as duas capturas | Exit 0, 24 draws e 2 shaders por captura | `../captures/probe/*/inspection/report.json` |
| Analisador/verificador: testes unitários | 43 testes passaram | `python -m unittest discover -s tools/edf_capture/tests` |
| Gravador assíncrono: harness C++ | Passou; 15 sessões, 2.021 registros, 6 blobs conferidos | `_build/edf_capture_writer_tests/artifacts/206929713770600` |
| Instalação/restauração | Passou em jogo fictício; backups, hashes, retomada e proteção de alterações | `_build/edf_deployment_tests/f378fb200ab74dcab442818653dadcdf` |
| Observador desativado | Probe exit 0, 24 draws, nenhum diretório de captura | `_build/edf_d3d9_probe/instrumented_x64/disabled-run.log` |
| Executável diferente no perfil, Bridge | Probe exit 0, 24 draws, observador não ativado | `_build/edf_d3d9_probe/instrumented_x86/wrong-executable.*` |
| Filtros combinados | Somente frame 1, draws 2–6; janela completa sem truncamento | `../captures/filter-test/1791380186891-18044-0/records.jsonl` |
| Hashes dos bundles | Todos os arquivos listados conferidos | `../packages/verification.json` |

Os filtros combinam `startFrame=1`, um frame, `firstDraw=2`, `lastDraw=7`, VS/PS SHA1 e hash do descritor de RT. Draws 0–1 são excluídas pela faixa e draw 7 pelo shader nulo. O probe continua executando suas 24 draws; a captura seleciona cinco.

Capturas finais:

- Runtime: `D:\EDF IA RTX REMIX\captures\probe\1791382669511-26212-0`.
- Bridge: `D:\EDF IA RTX REMIX\captures\probe\1791382682171-24748-0`.

Cada verificador confronta o bytecode original com `probe_shaders` da mesma execução e confere VB/IB, offsets, stride, índices/base, payload UP, normais/UV de entrada, F/I/B, stateblocks, DISCARD/NOOVERWRITE, World/View/Projection, FVF e metadados de passe. Os bancos de textura registram descritores; nenhum pixel é declarado capturado.

## Falhas encontradas e correções verificadas

O primeiro probe x86 parou após três draws: `GetStreamSource` ainda devolvia o VB após `DrawPrimitiveUP`. O Runtime x64 e o D3D9 do Windows devolviam null. O cliente Bridge não reproduzia no seu cache os efeitos UP. O cliente agora limpa stream 0 e, para a variante indexada, o IB após sucesso.

O transporte de `DrawIndexedPrimitiveUP` enviava `NumVertices * stride`. O servidor mantinha os índices originais e o Runtime lia `(MinVertexIndex + NumVertices) * stride`. O cliente agora envia o prefixo e o intervalo completo, com validação de overflow. O probe usa `MinVertexIndex=1`, um vértice sentinela e índices `(1,2,3)`; os bytes de todas as três posições/UV/normais passaram na comparação.

Depois dessas correções, as 24 draws completavam, mas World permanecia identidade no servidor. `MultiplyTransform` somente alterava o cache do cliente; o case do servidor estava vazio. Cliente e servidor agora transportam e aplicam a operação original. O cache também usa a ordem `pMatrix * State`, conforme a [documentação Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/d3d9/nf-d3d9-idirect3ddevice9-multiplytransform). Um teste com escala X=2 e translação X=0.1 exige translação resultante X=0.2. Ele passou no D3D9 do Windows e nos dois caminhos Remix. A captura do World +0.1 das draws principais também passou.

Arquivos dessas correções: `bridge/src/client/d3d9_device.cpp` e `bridge/src/server/main.cpp`. São correções do comportamento D3D9 demonstradas pelo probe, sem regras de interpretação do EDF. Cliente e servidor devem ser usados juntos.

Um teste adicional comparou `SetTransform` e `MultiplyTransform` durante gravação de stateblock. No D3D9 do Windows, SetTransform grava a escala e MultiplyTransform altera imediatamente a matriz ativa: antes de Apply, escala 1/translação 0.1; após Apply, escala 2/translação 0. O Runtime devolvia escala 1/translação 0 antes de Apply. A [lista oficial de métodos graváveis](https://learn.microsoft.com/en-us/windows/win32/api/d3d9/nf-d3d9-idirect3ddevice9-beginstateblock) inclui SetTransform e não inclui MultiplyTransform; a execução nativa demonstrou a diferença. `src/d3d9/d3d9_device.cpp` e o cache do cliente Bridge agora tratam MultiplyTransform como operação imediata. O evento informa `duringStateBlockRecording` separadamente de `recordedInStateBlock`.

As primeiras recompilações incrementais ficaram esperando o servidor de símbolos MSVC. Preservar e regenerar o par PCH/PDB do diretório de build resolveu a espera; código e arquivos do jogo não foram removidos. Os backups gerados permanecem no build.

## Limites da evidência

O teste não prova captura/reconstrução de um objeto do EDF, câmera do jogo, replacement ou path tracing. A renderização RTX permaneceu desligada. M1–M10 dentro do jogo ainda não foram demonstrados.

Não foi feito readback GPU de pixels de textura, interpretação de streams instanciados, skinning ou decoder do EDF. O manifesto mantém categorias e matrizes como `unknown`. Na captura real do probe, o scanner encontrou tanto o bloco usado pelo VS quanto outro candidato invertível sobreposto a uma constante `def`; isso confirma que aparência matemática sozinha não identifica uma transformação.

O build apresenta avisos de PDB ausente em dependências externas. O Runtime `debugoptimized` também registra objetos vivos ao encerrar o probe; esses avisos ocorreram com observador ativado e desativado, com exit 0. Sua causa não foi atribuída à instrumentação. Nenhuma captura final apresentou erro de escrita ou truncamento.

## Próximo dado necessário

Capturar gameplay do EDF: duas capturas com câmera parada e outra após mover a câmera. Os snapshots e bytecodes devem mostrar a cadeia que produz posição e UV, identificar o passe opaco e separar transformação do objeto de câmera/projeção. O Remix precisa desses espaços para reconstruir posições e gerar raios consistentes. Se o shader fornecer somente WVP, outras draws/passes ou dados CPU serão necessários para separar a câmera. O protótipo só começará após essa validação. Pixels de textura também precisarão de readback ou captura externa para provar a textura base.

Instalação e teste posterior: [IMPLEMENTATION.md](IMPLEMENTATION.md) e [README-deploy.md](README-deploy.md).
