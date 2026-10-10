# Incremento instrumental: limite configurável da fila

Problema observado: seis das dez capturas reais de EDF terminaram com `queue_limit`. O orçamento total em disco de 256 MiB não era o motivo; a fila assíncrona tinha limite fixo de 16 MiB. Aumentar somente esse limite pode preservar mais dados, sem interpretar geometria ou alterar a renderização.

## Mudança

- `src/util/util_compat_capture.h/.cpp`: terceiro argumento `maxQueueBytes`, padrão de 16 MiB. Rejeita valores incapazes de conter um item de blob vazio com hash, ou não representáveis por `size_t`. Mantém orçamento em disco independente, deduplicação, rejeição sem bloqueio por orçamento e drenagem dos itens aceitos.
- `src/dxvk/rtx_render/rtx_compat_capture.h/.cpp`: opção `rtx.compatCapture.maxQueueMiB = 16`, limitada a 1..256 MiB no adaptador. O perfil de teste usa 64 MiB.
- `capture_status` passa a registrar `max_queue_bytes` e `peak_queue_bytes`. A carga inclui a escrita em voo. O payload é liberado antes de descontar a carga, evitando que uma reserva concorrente reutilize o orçamento enquanto a memória antiga ainda vive.
- `tools/edf_capture_writer_tests/writer_tests.cpp` e README: verificações determinísticas adicionais, sem novo framework.
- `RtxOptions.md`: regenerado pelo Runtime com `DXVK_DOCUMENTATION_WRITE_RTX_OPTIONS_MD=1`; nova opção documentada na linha 108, com padrão 16 e faixa 1..256 MiB.

O limite contabiliza os itens/payloads ativos da fila; não é um limite de memória de todo o processo, das cópias fornecidas pelo chamador ou das tabelas de identidade da captura.

## Validação já concluída

Os testes C++17 standalone foram compilados com MSVC e passaram após o ajuste final de liberação de memória. Artefatos: `runtime/_build/edf_capture_writer_tests/artifacts/263138956655400`. Log: `queue-writer-tests.log`.

| Teste | Resultado |
|---|---|
| Blob de 20 MiB na fila padrão | Recusado com `queue_limit`; max 16 MiB, peak 0 |
| Mesmo tamanho na fila de 64 MiB | Aceito, copiado, drenado; tamanho/SHA-1 preservados |
| Deduplicação do blob grande | Um arquivo e `written_blobs=1` |
| Pico no caso de 64 MiB | 20.971.648 bytes, abaixo de 67.108.864 |
| Fila de 64 MiB com disco de 8 MiB | Recusado com `byte_limit`, sem enfileirar o payload |
| Fila de 1 byte | Erro reportado antes de criar manifesto; `finish()` repetido seguro |
| Testes anteriores | Hashes, escaping, concorrência, I/O, limites e finish preservados |

Uma leitura JSON independente verificou onze manifests, todos com `capture_status` terminal válido e `peak_queue_bytes <= max_queue_bytes`. Resultado: `queue-writer-verification.json`.

Comando reproduzível, em PowerShell na raiz do Runtime:

```powershell
.\tools\edf_capture\tests\writer_queue_tests.ps1
```

Esse wrapper inicializa MSVC x64 e executa exatamente o harness existente em `tools/edf_capture_writer_tests/writer_tests.cpp`, sem duplicar os casos. Também foi executado com sucesso; log `queue-writer-wrapper-tests.log`, artefatos `runtime/_build/edf_capture_writer_tests/artifacts/263491663537200`. Esses arquivos de fonte não estão ignorados pelo Git.

## Compilação

Comando: `meson compile -C _CompDebugOptimized_x64 -j2 d3d9`, com ambiente MSVC ativado pelo Meson, Vulkan SDK 1.4.363.0, `RTX_SHADER_BUILD_JOBS=2` e `NINJA` apontando o executável do venv. Não houve compilação do Bridge.

A primeira compilação encontrou o travamento PCH/PDB já observado neste ambiente. Somente a árvore do backend desse build foi interrompida. Os pares gerados dos módulos DXVK e D3D9 foram preservados em backups, sem apagar fontes nem mudar flags:

```text
_CompDebugOptimized_x64/src/dxvk/libdxvk.pdb.queue64-backup
_CompDebugOptimized_x64/src/dxvk/libdxvk.a.p/dxvk.pch.pch.queue64-backup
_CompDebugOptimized_x64/src/d3d9/d3d9.pdb.queue64-backup
_CompDebugOptimized_x64/src/d3d9/d3d9.dll.p/d3d9.pch.pch.queue64-backup
```

A primeira recuperação concluiu os objetos DXVK; uma segunda pausa no PDB D3D9 foi confirmada pelo handle do servidor desse build. Somente o par D3D9 foi novamente preservado, com sufixo `.queue64-retry-backup`, e recompilado. Logs: `queue-build-recovered.log`, `queue-build-final.log`.

**Compilação final concluída com exit 0.** Permaneceram os avisos LNK4099 de símbolos de debug ausentes nas dependências externas Sentry/mini_chromium; não houve erro de compilação ou link. DLL final: `runtime/_CompDebugOptimized_x64/src/d3d9/d3d9.dll`, 188.154.368 bytes, SHA-256 `36559e90e8d12626ec56f061971180e3f4eba53c30d5e21fa23b74341596fcfa`. A cópia em `runtime/_output/d3d9.dll` tem o mesmo hash.

## Probes controlados

Executado na pasta isolada com `DXVK_CONFIG_FILE` apontando `queue64.conf`. A captura `captures/probe-queue64/1791387855204-34052-0` contém 24 draws de três frames e terminou por `window_complete`, sem truncamento nem erro. `verify_probe.py` passou em **1.579 checks** de dados capturados, incluindo estados, ranges, shaders, constantes e variantes UP.

Footer observado:

```json
{"type":"capture_status","truncated":false,"reason":"","error":null,"accepted_bytes":229887,"max_bytes":67108864,"max_queue_bytes":67108864,"peak_queue_bytes":105465,"written_records":333,"written_blobs":38}
```

Esse primeiro teste confirmou os dados, mas não encerrou normalmente. A causa do trabalho/espera no encerramento não foi provada. O primeiro teste x86 com token restrito encontrou `0xc00000fd` durante CreateDevice. Os logs foram preservados e somente os processos dos probes foram encerrados. Não foi feito fix de renderer a partir dessas hipóteses.

Repetir os mesmos binários fora do token restrito concluiu ambos os testes normalmente:

| Caminho | Resultado | Pico da fila |
|---|---|---:|
| x64 direto | exit 0, 24 draws/3 frames, 1.584 checks aprovados | 107.925 bytes |
| x86 cliente + Bridge x64 | exit 0, 24 draws/3 frames, 1.584 checks aprovados | 87.929 bytes |

Ambos registram `max_queue_bytes=67108864`, `capture_end.reason=window_complete`, `truncated=false` e `error=null`. As saídas têm 333 registros e 38 blobs. Relatórios: `queue64-probe-x64-verification.json` e `queue64-probe-x86-verification.json` nesta pasta. Isso não estabelece a causa exata das falhas iniciais.

Nenhum arquivo do jogo ou pacote de diagnóstico existente foi alterado. O pacote novo está em `packages/queue64`, com os 376 arquivos dos dois manifests verificados por tamanho e SHA256.

O teste demonstra o gravador e o transporte controlado, não compatibilidade gráfica do EDF ou path tracing. A carga real do EDF com perfil filtrado/64 MiB ainda precisa da recaptura indicada em `analysis/edf/selected_draw/NEXT_CAPTURE.md`.
