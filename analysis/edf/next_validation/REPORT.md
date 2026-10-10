# Verificação das três recapturas EDF

**As três janelas estão completas e íntegras. A mesh de referência de 84 vértices/56 triângulos não aparece em nenhuma delas. Conforme a condição explícita do usuário, o decoder não foi iniciado.**

## Resultado por janela

| Ordem informada | Pasta | Frame | Draws selecionados | Blobs verificados | Janela/integridade | Mesh de referência |
|---|---|---:|---:|---:|---|---|
| Parada 1 | `1791398932979-38296-0` | 755 | 224 | 3166 | Aprovada | Ausente |
| Parada 2 | `1791398948329-38296-1` | 1182 | 224 | 3169 | Aprovada | Ausente |
| Após movimento | `1791398952733-38296-2` | 1292 | 209 | 3010 | Aprovada | Ausente |

Cada janela tem um `capture_end.reason=window_complete`, `completedFrames=1`, `truncated=false`, `error=null` e `capture_status` terminal. Os registros de draws/resultados, linhas gravadas e contagens de blobs conferem com os footers. Os 9.345 blobs passaram em SHA1 por conteúdo e tamanho, sem referências ausentes ou arquivos órfãos. Todos os ranges de geometria selecionados passaram pelo analisador. A fila efetiva é 67.108.864 bytes; picos: 12.370.284, 12.547.500 e 10.920.155 bytes.

## Falha da condição de presença

A referência permanece a captura antiga `1791384430306-28796-6`, frame7572/draw284. Identidades:

```text
VS       f098f457d9ae603bea5f84ec985d473c29cfb0c9
PS       44b92a29eb3e5f2ee6d53f02e14fb11a01511da8
POSITION c494580e5b4cd375e416dcd0c807a23344ac3c74 (672 bytes)
IB       8b4a8e527b38456f60762c5abf062efc37a2f6a3 (336 bytes)
```

Nas três janelas novas:

- Nenhuma draw tem `NumVertices=84` e `PrimitiveCount=56`.
- Nenhuma draw selecionada tem os ranges POSITION/IB de referência.
- A sequência exata de 672 bytes POSITION foi procurada em **todos os blobs**, inclusive como substring de intervalos maiores; houve zero ocorrências.
- Todas as draws selecionadas usam o par VS/PS esperado. O par também renderiza outras meshes e sozinho não identifica este objeto.

Existe uma draw de 56 triângulos em cada janela (draw988/draw988/draw928), mas utiliza **104 vértices únicos**, índices2196…2299. Ela não é a candidata de84vértices e não foi usada como substituição automática.

Assim, todas as três pastas falham especificamente no teste de presença da mesh exigida. Não há falha de integridade ou truncamento que explique essa ausência. Os snapshots filtrados não permitem afirmar se a entidade estava fora de vista, em outro local/passe, com outro LOD ou se mudou; nenhuma dessas hipóteses foi adotada como fato.

## Comparação possível das outras meshes

A comparação da **mesh exigida** está indisponível. Para aproveitar os dados sem trocar o alvo do protótipo, comparei apenas como evidência auxiliar o conteúdo recorrente do mesmo par de shaders.

As duas primeiras janelas têm a mesma multiset de224 combinações de conteúdo geométrico, base linear B e escala c9. Posições, atributos e índices são idênticos nessas associações. Foram encontrados154 pares de ocorrência única, correspondentes a16 pares distintos de transformação após deduplicar submeshes. Os registradores c0…c9 não são bit a bit iguais: maior diferença observada0,01272583. A câmera relativa tem deslocamento mediano de comprimento **0,00007328 unidade** e mudança de direção de **0,000576°**. Isso é coerente com câmera praticamente parada, mas não permite afirmar igualdade exata ou atribuir toda a diferença a arredondamento; sua causa não foi identificada.

Entre a terceira e cada uma das primeiras, há136 pares de ocorrência única com conteúdo/B/c9 invariantes e16 pares distintos de transformação. A relação comprovada no shader, `deltaCamera=(c7_depois-c7_antes)*B`, produz:

| Comparação | Delta mediano | Comprimento | Mudança de direção | Maior residual entre transforms |
|---|---|---:|---:|---:|
| 1→3 | `[-0.40173340, 1.04708860, -0.31300163]` | 1,16436862 | 8,185001° | 0,00001857 |
| 2→3 | `[-0.40170267, 1.04702757, -0.31302834]` | 1,16431031 | 8,184529° | 0,00004568 |

Esse consenso entre diferentes bases e posições locais de câmera sustenta movimento de câmera relativo às meshes recorrentes, condicionado à invariância da translação dos objetos. A fatorização de perspectiva foi validada matematicamente nas657draws selecionadas. Não foram recuperados World translation absoluta, identidade de entidade do engine ou sobreposição GPU/raster.

## JSON atualizado e decisão

[validation.json](<D:/EDF IA RTX REMIX/analysis/edf/next_validation/validation.json>) foi atualizado com `recaptureVerification`, resultados por janela, comparação auxiliar e `decoderGate.allowed=false`. O campo `failures` contém as três falhas de presença. As duas entradas antigas em `matchedDraws` foram conservadas como evidência histórica: **não são correspondências das recapturas**; `newRequiredMeshMatches=0` deixa isso explícito.

Nenhum código do Runtime foi alterado neste turno. Não houve compilação, instalação ou implementação de decoder/logs de aceitação RTX, pois a condição necessária falhou. O guia de recaptura foi corrigido para passar `records.jsonl` ao analisador.

Para liberar o experimento originalmente especificado, falta observar a mesh de referência nas janelas de controle. Seus dados devem aparecer nos snapshots de draw e ranges VB/IB de gameplay. É preciso retornar a um enquadramento que a contenha e recapturá-la, ou escolher explicitamente outro alvo recorrente e validar esse alvo antes da implementação. O par de shaders sozinho não resolve a seleção.

## Reprodução

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
$python = 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe'
$output = 'D:\EDF IA RTX REMIX\analysis\edf\next_validation'
foreach ($capture in Get-ChildItem -LiteralPath 'D:\EDF IA RTX REMIX\captures\edf-static' -Directory | Sort-Object Name) {
  & $python .\tools\edf_capture\analyze_capture.py (Join-Path $capture.FullName 'records.jsonl') `
    --output (Join-Path $output $capture.Name)
}
& $python .\tools\edf_capture\validate_edf_draw.py 'D:\EDF IA RTX REMIX\captures\edf' `
  --additional-capture-root 'D:\EDF IA RTX REMIX\captures\edf-static' --output $output
& $python (Join-Path $output 'audit\recapture_audit.py')
& $python (Join-Path $output 'compare_recaptures.py')
& $python (Join-Path $output 'finalize_verification.py')
```

Evidência detalhada: [auditoria](<D:/EDF IA RTX REMIX/analysis/edf/next_validation/audit/audit.json>), [comparação](<D:/EDF IA RTX REMIX/analysis/edf/next_validation/comparison.json>) e os três `report.json` nas subpastas desta análise. O script de comparação não contém um decoder de renderização.
