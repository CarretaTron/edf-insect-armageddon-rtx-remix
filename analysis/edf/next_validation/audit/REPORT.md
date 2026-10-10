# Auditoria independente das três recapturas

As três janelas estão completas e íntegras. A mesh específica anteriormente validada **não aparece em nenhuma delas**. Portanto, o critério solicitado para iniciar `EdfStaticDrawDecoder` não foi satisfeito.

| Captura | Frame | Procedimento informado | Draws selecionadas | Blobs verificados | Encerramento | Truncamento |
|---|---:|---|---:|---:|---|---|
| `1791398932979-38296-0` | 755 | Primeira, câmera parada | 224 | 3166 | `window_complete` | `false` |
| `1791398948329-38296-1` | 1182 | Segunda, sem mover câmera | 224 | 3169 | `window_complete` | `false` |
| `1791398952733-38296-2` | 1292 | Terceira, após mover câmera | 209 | 3010 | `window_complete` | `false` |

Todas têm `completedFrames=1`, `error=null`, `max_queue_bytes=67108864` e exatamente um rodapé `capture_status` terminal. Os picos de fila foram, respectivamente, **12370284**, **12547500** e **10920155** bytes. Não há erro de parsing JSONL, blob ausente, blob órfão ou discrepância de tamanho/SHA1. Os **9345 blobs** no disco foram lidos e tiveram SHA1 verificado; todas as referências únicas foram verificadas também. `written_records` corresponde ao número de linhas anterior ao próprio `capture_status`.

## Ausência da mesh de referência

Referência: captura `1791384430306-28796-6`, frame 7572, draw 284, 84 vértices e 56 triângulos.

- VS: `f098f457d9ae603bea5f84ec985d473c29cfb0c9`.
- PS: `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`.
- POSITION, 672 bytes: `c494580e5b4cd375e416dcd0c807a23344ac3c74`.
- Índices, 336 bytes: `8b4a8e527b38456f60762c5abf062efc37a2f6a3`.

Todas as 224/224/209 draws selecionadas utilizam o mesmo par de shaders esperado, demonstrando que o filtro capturou sua categoria. Porém, em cada uma das três janelas:

- Nenhuma draw possui simultaneamente `numVertices=84` e `primitiveCount=56`.
- Nenhum intervalo POSITION possui o hash da referência.
- Nenhum intervalo de índices possui o hash da referência.
- Nenhuma draw corresponde à chave completa da geometria original.
- A sequência exata de **672 bytes** de POSITION foi procurada como substring em **todos os blobs**, incluindo blobs maiores: **zero ocorrências**.

Essa busca adicional descarta um falso negativo causado apenas por outra divisão de intervalos ou pelo hash de um blob maior. O fato de os shaders serem iguais não identifica a mesma mesh.

Não é possível distinguir, por essas janelas, entre mudança de cenário, ausência do objeto no frustum/passe selecionado ou outra causa específica do jogo. Uma janela completa comprova que o gravador terminou o frame selecionado; não comprova que todas as entidades do nível foram desenhadas. Os filtros restringem snapshots e buffers às draws do par de shaders escolhido.

Para esta mesh, não existem três observações positivas que permitam comparar estabilidade e movimento de câmera. Não foi criado decoder nem alterado o Runtime nesta auditoria.

## Reprodução

```powershell
& 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe' `
  'D:\EDF IA RTX REMIX\analysis\edf\next_validation\audit\recapture_audit.py'
```

Os detalhes por arquivo, referências e buscas de sequência estão em [audit.json](audit.json). O script é de leitura: grava somente seus resultados nesta pasta.
