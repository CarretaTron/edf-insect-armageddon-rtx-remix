# Revisão da documentação e das evidências

Revisão de `REPORT.md`, `validation.json`, `../next_validation/validation.json`, `profiles/edf-static-prototype/TESTAR.md` e `runtime/tools/edf_static_gpu_replay/finalize_validation.py`. Esta revisão não executa o EDF, não altera o Runtime e não constitui uma evidência GPU adicional.

## Distinções preservadas

- O relatório identifica `c0…c3` como transformação composta local→clip pelo bytecode, em vez de nomear automaticamente World/View/Projection separadas.
- A fatorização usa W identidade como âncora e registra que a translação absoluta original do objeto não é recuperável unicamente.
- As duas primeiras capturas apresentam deriva pequena de câmera; a documentação não promete igualdade bit a bit das constantes.
- Os hashes de descritor de s0 não são apresentados como hashes dos texels. Os pixels originais não foram capturados.
- Os seis componentes desconectados são um batch de uma draw, sem alegar seis instances ou nomes de entidades do jogo.
- O gate offline autoriza apenas a candidata de 252 vértices utilizados/144 triângulos e o par exato de shaders.
- `TESTAR.md` deixa o teste dentro do EDF pendente. O pacote não deve ser confundido com uma instalação ou execução já realizada no jogo.
- Os logs de BLAS/TLAS são descritos como comandos registrados pelo Runtime, sem alegar conclusão por fence nem validação visual do path tracing.
- `rasterOnly` é descrito como preparação da cena sem despacho de path tracing.

## Pontos a concluir na entrega

1. Atualizar `next_validation/validation.json` com `finalize_validation.py` após os replays finais. O registro atualmente disponível ainda tem `implementation.originalRasterPreserved=true` e `gpuReplay=[]`; ele deve ser substituído pela distinção `preservesRasterByDesign`/`controlledReplayRasterVerified` e pelas evidências efetivamente obtidas.
2. Documentar o SHA256 do DLL final e os dois transportes exercitados: device x64 direto e cliente x86/Bridge com servidor x64. Indicar os arquivos de verificação e comparação raster correspondentes.
3. Registrar que os buffers, índices, bytecodes e constantes do replay provêm das três capturas reais, mas suas texturas são sintéticas. A comparação raster desse replay demonstra preservação no experimento controlado; ela não é uma comparação dos texels originais dentro do EDF.
4. Descrever separadamente as correções mínimas de tipos SPIR-V encontradas na captura genérica e o gate da captura programável usado pelo perfil CPU-decoded. Informar os resultados reais de `spirv-val` e compilação.
5. Se houver avisos de objetos vivos no encerramento do probe nos dois modos, registrá-los como observação do shutdown controlado, sem apagá-los nem classificá-los automaticamente como falha de rendering.
6. Deixar explícito que o teste no EDF e a verificação visual de sua textura original continuam pendentes, com comandos em `TESTAR.md`.

## Proveniência do finalizador

O ponto de proveniência encontrado na revisão foi corrigido nos scripts: `verify_runtime.py` registra SHA256 e tamanho do renderer DLL do caso e seu transporte. Quando há Bridge, ele seleciona `.trex/d3d9.dll` e nunca usa o hash do proxy x86 como fallback. Um caso real sem renderer válido falha; fixtures sintéticas sem DLL mantêm a proveniência explicitamente indisponível, sem inferir um hash.

`finalize_validation.py` exige SHA256/tamanho concordantes com `--runtime` em todas as evidências GPU e exige que a pasta positiva da comparação raster corresponda a uma dessas execuções verificadas. O hash identifica o arquivo do caso depois da execução, não uma medição feita pelo loader dentro do processo; essa origem aparece no campo `measurement`. A execução final deve regenerar os verificadores após atualizar os DLLs, sem reutilizar JSONs de outra versão. O finalizador não exige por si só um par x64+x86: os dois transportes continuam uma responsabilidade explícita da entrega.

Os 14 testes de `runtime/tools/edf_static_gpu_replay/test_provenance.py` passaram: seleção do renderer em vez do proxy, falta de fallback, hashes/tamanhos divergentes, pasta raster sem replay correspondente e recusa de fixture como evidência real, além da aceitação com rótulo sintético da fixture completa anterior. Estes testes verificam os scripts; não executam um replay GPU.

Não foi encontrada uma alegação documental de World/View/Projection originais recuperadas unicamente ou de que o protótipo já passou dentro do EDF.

## Fechamento da entrega

Os pontos pendentes acima foram consolidados em `IMPLEMENTATION.md` e `next_validation/validation.json` após os replays finais. As duas evidências GPU e as duas comparações raster concordam com o DLL `7b8ab28eee0838ee2e9e018663e7f911df6d311d7b25437587fb5cffc7cdb98d`. Pacotes e SPIR-V finais aprovados. O estado final continua `compiled_controlled_replay_passed_game_test_pending`.
