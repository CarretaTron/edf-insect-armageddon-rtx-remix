# EDF: evidência entre as capturas F10

Análise somente dos arquivos capturados. Nenhum código do Runtime, perfil de jogo ou buffer original foi alterado. Não há correspondência confiável entre diretórios e a intenção de ficar parado/mover; os frames são identificados pelo conteúdo e seus transforms.

## Integridade

Foram lidos os dez diretórios, totalizando **7.907 draws**. Todos os JSONL são legíveis, todos os blobs referenciados têm tamanho e SHA1 corretos e todos os 29.450 arquivos `.bin` têm SHA1 igual ao nome. `capture_status` e `capture_end` diferenciam integridade dos dados escritos de conclusão da janela.

| Diretório | Frame | Draws | Janela |
|---|---:|---:|---|
| `1791384177713-28796-0` | 215 | 3 | Completa |
| `1791384179484-28796-1` | 267 | 3 | Completa |
| `1791384287020-28796-2` | 3430 | 933 | Prefixo, `queue_limit` |
| `1791384289663-28796-3` | 3473 | 1.076 | Prefixo, `queue_limit` |
| `1791384292418-28796-4` | 3520 | 1.060 | Prefixo, `queue_limit` |
| `1791384295010-28796-5` | 3564 | 1.123 | Prefixo, `queue_limit` |
| `1791384430306-28796-6` | 7572 | 732 | Completa |
| `1791384433361-28796-7` | 7644 | 752 | Completa |
| `1791384443863-28796-8` | 7941 | 1.107 | Prefixo, `queue_limit` |
| `1791384446843-28796-9` | 7987 | 1.118 | Prefixo, `queue_limit` |

As janelas truncadas registraram 18,5–23,6 MiB, abaixo de `max_bytes=256 MiB`: o motivo registrado é **limite de fila**, não limite total de arquivo. -2 contém um blob não referenciado e -8 contém seis; todos têm hashes corretos. Dados ausentes depois da interrupção não demonstram ausência de passes ou meshes. -0/-1 têm apenas três draws e não apresentam o shader candidato.

Os snapshots também declaram `textureContentCaptured=false`; texturas têm metadados e IDs de sessão, sem pixels capturados. `descriptorHash` não é identidade de conteúdo e não permite reconhecer albedo por si só.

## Candidato pequeno e recorrente

A geometria abaixo ocorre uma vez no passe que usa o par de shaders analisado em cada uma das duas janelas de gameplay completas:

- -6, frame **7572**, **draw 284**, stateId `b17beea8a234b8cd4bb032696983af188a30dbc2`.
- -7, frame **7644**, **draw 281**, stateId `5321739f3e20eda04dbf6b7ca36af6cf1cd12915`.
- VS `f098f457d9ae603bea5f84ec985d473c29cfb0c9`; PS `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`.
- `DrawIndexedPrimitive`, `TRIANGLELIST`, **56 triângulos, 168 índices de 16 bits, 84 vértices**, base 0, startIndex 4728, índices efetivos 0…83.
- Declaration `6ff4217f660b280ef21cd86f83889aa93219aaa6`: stream0 BINORMAL UBYTE4; stream1 NORMAL UBYTE4; stream2 POSITION0 SHORT4; stream3 TANGENT UBYTE4; stream4 TEXCOORD0 SHORT2. Strides 4/4/8/4/4; frequency 1. Streams5/6 estão ligados, mas não pertencem à declaração usada por esse VS.
- Posição stream2 resource2395/version1, offset17976, 672 bytes, SHA1 `c494580e5b4cd375e416dcd0c807a23344ac3c74`.
- UV stream4 resource2398/version1, offset8988, 336 bytes, SHA1 `132df6203bfddc1c41fbee3e81a43645d04f8d66`.
- Índices SHA1 `8b4a8e527b38456f60762c5abf062efc37a2f6a3`, 336 bytes, byteOffset9456. Os bytes usados permanecem idênticos nos dois frames.
- `alphaBlend=0`, `alphaTest=0`, `zEnable=1`, `zWrite=1`, `colorWrite=15`, cull2. Isso identifica uma draw opaca pelos estados D3D9; a semântica completa do PS deve ser comprovada pelo bytecode.
- RT0 resource4831, A8R8G8B8, 1920×1080, **offscreen**; depth resource628 D24S8. Texture s0 resource5078, descriptor `93ac652188094ebb`, sem conteúdo disponível.

Não existem inputs de bones/pesos neste VS. O bytecode efetivamente calcula posição por uma transformação linear de três coordenadas SHORT4 multiplicadas por `c9.x`; a constância dos bytes não basta sozinha, mas aqui a execução não aplica skinning ou deformação temporal à posição.

Local POSITION bounds decodificados: x[-9.94921875,9.951171875], y[-4.580078125,6.2138671875], z[0,24.845703125]. `c9.x=1/1024`, `c9.y=1/2048`, `c9.z=1/127`, `c9.w=1`. UV bounds: u[0.0068359375,0.9931640625], v[0.00634765625,0.9931640625]. Os vetores UBYTE4 remapeados para [-1,1] têm comprimentos próximos de um (normais 0,99506…1,00374), coerentes com sua quantização; a interpretação de eixo e orientação deve preservar a ordem do bytecode.

A mesma posição e os mesmos índices aparecem em **draw56** de -6 e de -7, usando VS `100fb804e4cbe4eec5bd02fe9a604bd4c5ede30d` / PS `6bfe7adb3a8cae9fd4248c2fd2c5174095019411`, declaration `12261bb81397ed2d17a36e8ee8d5b65e43b7f506`, RT4818. O UV não está ligado nesse passe. Isso é correspondência de geometria entre passes, ainda sem atribuir categoria funcional ao primeiro passe.

Busca pelo hash de posição, hash de índices e pela sequência de 672 bytes dentro de todos os streams POSITION0/SHORT4/stride8 disponíveis só encontrou essas quatro draws. **Não há terceiro frame positivo do mesmo candidato nos dados disponíveis.** Não se pode concluir que ele foi ocultado/cullado nos seis prefixos truncados.

## Separação espacial validada entre draws

A desassembly do VS preserva os símbolos `$veLocalToProjection`, `$veLocalToWorld`, `$veCameraLocalPosition` e `$veInvFixedPoint`. As instruções, além dos nomes, demonstram:

```text
p = v2.xyz * c9.x
q = p.x*c0 + p.y*c1 + p.z*c2 + c3
B = rows(c4.xyz, c5.xyz, c6.xyz)
vetor de câmera emitido = (p - c7.xyz) * B
A = rows(c0, c1, c2)
M = inverse(B) * A
k = c7.xyz * A + c3
q = (p - c7.xyz) * B * M + k
```

A identidade algébrica por draw é necessária, mas **a validação não termina nela**. A fatoração produziu o mesmo `M` para todos os objetos capturados no RT4831 dentro de cada frame, apesar de seus diferentes B/c7. Em -6 há 74 draws f098 e o maior range de um elemento de M é **1,70e-7**; em -7 há 70 e o range é **1,64e-7**. Os valores de k são próximos de [0,0,-0.25,0], com ranges máximos **1,97e-4** e **2,63e-4**, compatíveis com cancelamento de floats após combinar coordenadas grandes. A matriz B do candidato e suas constantes c8/c9 são exatamente invariantes nos dois frames; c0…3 e c7 mudam.

Os quatro primeiros elementos de `projectionColumnNorms` do candidato são aproximadamente [1.732045,3.079192,1.000061,1]. As colunas x/y/w são ortogonais até ~2,72e-8, a coluna z é proporcional a w com fator ~1.00006102036, e o residual dessa proporcionalidade nos 74/70 exemplos é menor que 6,03e-8. Essa estrutura sustenta uma projeção perspectiva e um transform de câmera comum, sem afirmar que os registradores armazenam V e P separadamente. Determinante/sinais precisam ser tratados explicitamente para a convenção usada pelo Remix.

**c4…6 não contém World translation utilizada pelo shader.** Seus `.w` não são lidos. O shader usa apenas B para vetores e usa c7 como posição da câmera no espaço local. Logo uma origem global absoluta e a tradução original CPU do objeto continuam indeterminadas sem fixar uma referência. Um protótipo pode escolher uma origem comum de forma documentada e reconstruir posições relativas; não deve interpretar o `.w` de B como tradução.

## Movimento entre frames e recorrência

Para conteúdo, escala c9 e B invariantes:

```text
delta = (c7_frame2 - c7_frame1) * B
```

Se aquele objeto não transladou, delta é o deslocamento da câmera no espaço comum. A hipótese é testada por concordância entre **pares B/c7 distintos**, deduplicando submeshes e draws com diferentes pixel shaders que usam o mesmo transform.

| Frames | Matches de geometria únicos | Pares B/c7 distintos | Delta mediano | Máx. residual absoluto |
|---|---:|---:|---|---:|
| 3430→3473 (-2→-3) | 114 | 30 | [-1.741124,-6.167844,-0.124237] | 1.19e-4 |
| 3473→3520 (-3→-4) | 161 | 69 | [2.741997,-5.073181,-0.084814] | 6.15e-5 |
| 3520→3564 (-4→-5) | 163 | 71 | [-6.624046,-0.549316,-0.016262] | 3.83e-5 |
| 7572→7644 (-6→-7) | 18 | 10 | [-0.683017,-0.269897,-0.002226] | 3.75e-5 |
| 7941→7987 (-8→-9) | 168 | 48 | [0.071134,0.032288,0.000711] | 6.03e-5 |

O candidato 284→281 fornece [-0.683024222,-0.269897897,-0.002225876], dentro do consenso dos outros nove pares. Essa concordância sustenta geometria estática e câmera variável nos frames observados; não fornece intenção do jogador nem posição mundial absoluta.

Entre grupos de frames muito separados (-2…-5 versus -8/-9) existem 7–9 pares de consenso e alguns outliers grandes (até ~709 unidades). Geometria + B pode ser reutilizada por instâncias distintas, e a presença única no prefixo capturado não prova identidade persistente. O relatório conserva esses outliers em vez de forçar todos os modelos para um único movimento. Não há match conservador único entre -6/-7 e os demais frames.

O agrupamento genérico `cross_capture.json` tem 301 grupos recorrentes em janelas completas e 183 que passam filtros de estados opacos/sem skinning declarado. Ele usa todos os streams ligados e representa **filtro inicial**, não 183 objetos estáticos comprovados; o analisador específico usa somente streams da declaração, packing, escala e dados efetivamente usados pelo shader.

## Limites e próximo teste

Este resultado comprova a decodificação e uma fatoração espacial offline com evidência entre objetos e frames. Ainda não demonstra recebimento pelo SceneManager/RTX, replacement ou path tracing. Faltam conteúdo da textura base, terceiro frame do mesmo candidato, correlação visual da sua silhueta e validação das convenções de projeção do Runtime.

Próximo teste pequeno: capturar mais dois ou três frames filtrados pelo par f098/44b92 ou pelo conteúdo do candidato, preservando uma janela completa; gerar wireframe/UV da draw284, conferir silhueta sobre a imagem raster e reconstruir em uma origem comum com referência fixa. Aumentar somente `max_bytes` não corrige o `queue_limit`; reduzir o filtro de eventos/draws ou revisar o orçamento da fila será necessário para obter a janela completa com volume limitado.

Arquivos de evidência: `cross_capture.json`, `all_blob_audit.json`, `target_matrix_analysis.json`, `camera_motion_pairs.json`, `candidate_all_passes.json`, `candidate_position_sequence_search.json`. Scripts reproduzíveis nesta mesma pasta: `inspect_captures.py`, `target_matrix_analysis.py`, `camera_motion_pairs.py`.
