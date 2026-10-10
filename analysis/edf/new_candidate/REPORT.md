# EDF: nova candidata única validada nas três recapturas

A candidata escolhida tem **252 vértices utilizados, 144 triângulos, uma ocorrência em cada frame**. Ela aparece nas draws **952 / 952 / 898**, nos frames **755 / 1182 / 1292**. A geometria, os índices, os cinco atributos ativos, a base linear e as escalas de decodificação são idênticos nas três observações. O shader comprova a recuperação de POSITION, UV e NORMAL.

A autorização posterior do usuário permite trocar a mesh antiga de 84/56. A ausência da mesh antiga continua registrada no relatório anterior; o novo gate aplica-se exclusivamente à candidata de 252/144. A autorização não permite interpretar outras meshes pelo mesmo shader.

## Integridade e procedimento

| Ordem informada pelo usuário | Captura | Frame | Draws selecionadas | Blobs verificados | Resultado |
|---|---|---:|---:|---:|---|
| Câmera parada, primeira | `1791398932979-38296-0` | 755 | 224 | 3.166 | Completa e íntegra |
| Câmera parada, segunda | `1791398948329-38296-1` | 1182 | 224 | 3.166 | Completa e íntegra |
| Após mover a câmera | `1791398952733-38296-2` | 1292 | 209 | 3.013 | Completa e íntegra |

A auditoria desta etapa voltou a verificar **9.345 blobs**: SHA1 por conteúdo, tamanhos de cada referência, existência dos arquivos e ausência de órfãos. Cada janela encerra com `window_complete`, `completedFrames=1`, `truncated=false`, `error=null` e fila máxima de 67.108.864 bytes. Os contadores do footer concordam com os registros e arquivos. Os SHA256 dos `records.jsonl` constam em [integrity.json](./integrity.json) para detectar uma auditoria desatualizada.

## Comparação das candidatas

Foram comparadas **133 identidades de geometria presentes positivamente nas três janelas**. Dessas, **122 aparecem exatamente uma vez em cada janela**. Dez identidades têm `PS_c1.rgb=(1,1,1)`; nove têm ocorrência única. Isso simplifica material e associação de instância.

O ranking considera conteúdo dos cinco streams declarados, índices, estabilidade de `c4…c6`/`c9`, frequências, estados de opacidade, semântica material e projeção. Não usa ponteiros, draw number ou contagem semelhante à mesh antiga como reconhecimento. Todas as alternativas simples de até 200 triângulos e ocorrência única foram projetadas nos três frames. As maiores continuam documentadas, sem alegar uma validação completa que não foi feita.

| Draws nas três janelas | Vértices / triângulos | Observação técnica | Decisão |
|---|---:|---|---|
| 913 / 913 / 865 e outras oito alternativas | 20 / 10 | Pequenas, mas `PS_c1` colorido: o albedo depende também do alpha de s0. | Exigem conversão material adicional. |
| 1137…1143 / 1137…1143 / 1082…1088 | 42 / 24 | `PS_c1` branco, porém sete ocorrências de conteúdo por frame. | Evitar associação de instâncias nesta fase. |
| 988 / 988 / 928 | 104 / 56 | Recorrente e opaca, mas `PS_c1.rgb≈(0,70588;0,74510;0,69020)` colore a parcela ponderada pelo alpha. | Não escolhida apenas por ter 56 triângulos. |
| 868 / 868 / 819 | 230 / 120 | Material simples, base identidade; parte dos vértices fica fora da viewport. | Menor em polígonos, pior para a primeira confirmação visual. |
| **952 / 952 / 898** | **252 / 144** | **Material simples, base identidade, faixa contígua de vértices, todos os vértices dentro da viewport nos três frames.** | **Escolhida.** |
| 954 / 954 / 900 | 279 / 157 | Mesmas vantagens material/espacial, maior. | Alternativa válida, sem vantagem sobre a escolhida. |
| 950 / 950 / 896 | 294 / 168 | Mesmas vantagens material/espacial, maior. | Alternativa válida, sem vantagem sobre a escolhida. |

Detalhes de todas as identidades estão em [candidate_ranking.json](./candidate_ranking.json). A interpretação de objeto estático é apoiada nas três observações; não identifica o nome do objeto nem garante que ele nunca será destruído ou sofrerá LOD.

## Identidade e layout da escolhida

- VS original SHA1: `f098f457d9ae603bea5f84ec985d473c29cfb0c9`.
- PS original SHA1: `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`.
- Declaração: `6ff4217f660b280ef21cd86f83889aa93219aaa6`.
- Geometry key de análise: `925aa18481228b01392a574dbe28ba4e21aa1616`.
- `DrawIndexedPrimitive(TRIANGLELIST,0,0,882,70707,144)`: base 0, mínimo declarado 0, 882 vértices declarados, startIndex 70.707.
- **Faixa realmente utilizada: índices 630…881, todos os 252 vértices dessa faixa utilizados**. O decoder deve rebasing para 0…251; `numVertices=882` não significa que 882 vértices entram nessa mesh.
- 432 índices UINT16, 864 bytes; SHA1 original `2ecc0d2d1046f418d56ea51bbcca29579a04a937`.
- Após subtrair 630, SHA1 dos 432 índices UINT16: `3b9d76860a797e5399e6302e26f307e9e9681926`.

| Stream | Semântica / registrador | Formato / stride | Bytes usados | SHA1 dos bytes utilizados |
|---:|---|---|---:|---|
| 0 | BINORMAL0 / v0 | UBYTE4 / 4 | 1.008 | `392f544f133f951dea21d8177cf549c553608827` |
| 1 | NORMAL0 / v1 | UBYTE4 / 4 | 1.008 | `7c6fcffa660096291a371651cd73a85bfa5aa1ce` |
| 2 | POSITION0 / v2 | SHORT4 / 8 | 2.016 | `f3ddbe4c53df3aea29e2cad8d305844c95c7dc4c` |
| 3 | TANGENT0 / v3 | UBYTE4 / 4 | 1.008 | `d6b757b9a822e87a7983c8dca148d55ff813ed77` |
| 4 | TEXCOORD0 / v4 | SHORT2 / 4 | 1.008 | `f4d8a45daf8dcb614756f4bbd45a13d18e390b49` |

Todos os elementos têm offset 0, método 0, usageIndex 0; todas as frequências ativas são 1. Streams 5/6 residuais não são inputs declarados desse VS. Não há pesos ou índices de bones no layout. O VS não possui skinning, loops, branches, acesso relativo a constantes ou vertex texture fetch.

A mesh escolhida é um **batch estático com seis partes desconectadas já representadas nos buffers**, cada uma com 42 vértices de atributo e 24 triângulos. Após unir posições exatamente iguais, cada parte tem 15 pontos geométricos. Isso não usa instancing de streams ou deformação: uma única draw fornece o conjunto completo. O protótipo deverá criar uma RasterGeometry/instance para esse batch; não declara que ele corresponde a uma entidade única nomeada no jogo.

## Prova de posição, UV e normal pelo bytecode

O VS capturado é SM3, tem 26 instruções aritméticas em linha reta e foi executado no interpretador offline para **cada um dos 252 vértices, nos três frames**. O conteúdo dos bytecodes foi verificado contra os SHA1 acima antes de usar a desassemblagem.

As constantes de decodificação são iguais nos três frames:

```text
c9 = (0.25, 1/2048, float32(1/127), 1)
B  = rows(c4.xyz, c5.xyz, c6.xyz) = identidade
p  = signed SHORT4.xyz * c9.x
q  = p.x*c0 + p.y*c1 + p.z*c2 + c3
o0 = q
uv = signed SHORT2.xy * c9.y
o1.yz = uv
o1.x = (q.z-c8.x)*c8.y             // fog, não U
n  = unsigned UBYTE4.xyz*c9.z-c9.w
o4.xyz = n*B                      // TEXCOORD3, normal utilizada pelo PS
o5.xyz = (p-c7.xyz)*B              // vetor câmera→superfície
```

Os erros entre o interpretador e essas equações são **zero** para posição, UV e normal em todos os vértices e frames. A quarta componente de SHORT4 não entra na posição; a contribuição homogênea vem de `+c3`. SHORT2 e SHORT4 são inteiros com sinal, sem normalização automática; UBYTE4 é unsigned bruto. Isso corresponde às [definições oficiais dos formatos D3D9](https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3ddecltype).

Bounds decodificados: X `[728,838]`, Y `[85,75;456,75]`, Z `[-2,5;29]`. UV: U `[0,00244140625;0,99658203125]`, V `[0,00439453125;0,99609375]`. Comprimentos das normais entre `0,999999993` e `1,003743992`, compatíveis com quantização. As 144 faces não têm área geométrica ou UV nula. Áreas geométricas entre 1,375 e 290,0625; cos entre normal geométrica pela ordem dos índices e média das normais dos vértices entre **0,996538308 e 1**. Nenhuma face tem normal invertida segundo esse teste.

## Transformação e câmera: o que foi comprovado

**c0…c3 são local→clip.** Isso decorre das instruções que escrevem `o0`; não se assume que sejam quatro matrizes separadas. Os `SetTransform` World/View/Projection capturados estão identidade e não explicam essa draw.

A base `B=c4…c6` é identidade nas três janelas. O VS usa `c7` separadamente no vetor câmera→superfície. A fatorização adota uma **âncora fixa com W identidade** e deriva uma view de rotação própria RH e uma projeção perspectiva. Não recupera unicamente a translação original do objeto no engine. O mesmo ponto pode ser representado por outra origem global sem mudar a projeção.

Para `A=rows(c0…c2)` e `k=c7*A+c3`, verifica-se:

```text
q = (p-c7)*A+k
k ≈ (0,0,-0.25,0)
M = inverse(B)*A = A
```

As colunas X/Y/W de M são ortogonais; a coluna Z é paralela à W. A câmera derivada respeita determinant≈+1, perspectiva RH, ausência de shear e reverse-Z. O valor de `c7` transforma-se para essa mesma base espacial. O teste exige a concordância independente de `c7`, em vez de ajustar arbitrariamente a translação para qualquer matriz parecida com perspectiva.

| Comparação | Geometria, índices, UV e normal | Base B / c9 | Deslocamento da câmera na âncora | Rotação do forward | Maior deslocamento projetado |
|---|---|---|---:|---:|---:|
| Primeira→segunda | Idênticos | Idênticos | 0,000073106 | 0,000576369° | 0,0167663 px |
| Primeira→terceira | Idênticos | Idênticos | 1,164368768 | 8,185000817° | 253,197837 px |
| Segunda→terceira | Idênticos | Idênticos | 1,164310402 | 8,184528956° | 253,181092 px |

As primeiras capturas são consistentes com câmera praticamente parada, com deriva pequena; **não são bit a bit iguais nas constantes de câmera**. Primeira→terceira tem delta `(-0,401733398; +1,047088623; -0,313002110)`. Esse delta concorda com a mediana de **16 pares distintos de transformação**, com residual máximo da candidata de `4,77e-7` por componente. O residual máximo entre todos os pares é `1,86e-5`. A consistência entre outras geometrias favorece movimento de câmera sobre movimento independente desta mesh.

A matriz composta da fatorização reproduz a projeção do VS com erro máximo **0,000042035 pixel** nos três frames, em aritmética offline. Também rodei o probe com as próprias classes `Matrix4` e `decomposeProjection` do Runtime: os três casos passam; erro máximo de composição float é **0,000099990 pixel**. [Resultados do helper real](./runtime_projection_verification.json).

A projeção muda ligeiramente: fov vertical em radianos `0,628169 / 0,628169 / 0,628830`. O decoder deve calculá-la por draw e validá-la, sem hardcodar FOV. A estimativa de near é cerca de 0,25; a de far cerca de 4.096, mas a inversão de `depthA-1` é mal condicionada e não justifica um valor exato fixo.

Esses testes demonstram coerência matemática dos dados e do caminho de matrizes. Ainda não são uma comparação com pixels originais nem prova de aceitação de uma instance RTX dentro do EDF.

## Passe opaco e textura

Estados nas três ocorrências: ZENABLE=1, ZWRITEENABLE=1, ALPHATESTENABLE=0, ALPHABLENDENABLE=0, CULLMODE=2 (`D3DCULL_CW`), color-write RGBA=15 nos três targets. O PS não executa `texkill` e escreve `oC0.w=1`. Não é necessário interpretar o alpha de s0 como transparência.

O PS lê `texld r0,v0.yzzw,s0`: os UV estão em TEXCOORD0.yz. Antes da iluminação, o diffuse é:

```text
sample.rgb * ((1-sample.a)*PS_c1.rgb + sample.a)
```

`PS_c1.rgb=(1,1,1)` nas três capturas simplifica isso exatamente para `sample.rgb`, independentemente do alpha. Esta é a razão material para preferir a candidata escolhida às menores coloridas. s1/s2 são cubemaps usados para iluminação/reflexão original; serão excluídos do material RTX mínimo.

s0 observado: resource 4928, descriptor hash `af52c1a348fe8e4e`, **DXT5, 128×128, oito mips, uma camada, uso 0, pool 0**. O formato numérico 894.720.068 corresponde aos bytes FourCC `DXT5`. O mesmo binding/descritor/sampler aparece nos três frames. Sampler U/V clamp (3), min anisotropic (3), mag linear (2), mip linear (2), MaxAnisotropy=2, sRGB=0. O descritor não é um hash dos pixels: **os texels não foram capturados**. O Runtime deve usar a textura s0 já vinculada ao jogo; uma textura sintética em replay só testa o encadeamento material, não a aparência original.

O passe usa três RTs offscreen A8R8G8B8 de 1920×1080, resources 4908/4909/4910, depth D24S8 resource 4898. A draw raster original deve ser executada integralmente para preservar o conteúdo dos MRTs consumido pelos passes posteriores.

## Contrato do protótipo único

[profile_manifest.json](./profile_manifest.json) define a whitelist por executável/fingerprint, SHA1 dos bytecodes, declaração, conteúdo dos cinco atributos, índice utilizado, escala, base, material opaco e passe MRT. Os números de draw, IDs de recurso e offsets das capturas são evidências de proveniência; não devem ser usados como reconhecimento.

O decoder mínimo converte os 252 vértices para FLOAT3 posição, FLOAT3 normal e FLOAT2 UV, rebasa os 432 índices, estabelece a âncora fixa e a câmera validada no `DrawCallState`, e usa somente s0 como albedo opaco. O hash da geometria decodificada exclui as constantes móveis da câmera para essa draw reconhecida. A draw raster original continua sendo emitida. Com o perfil experimental habilitado, as demais draws do EDF permanecem no raster sem reconstrução RTX. O perfil desabilitado e outros executáveis mantêm o caminho genérico.

Depois disso, o adapter poderá reutilizar `RasterGeometry → SceneManager → InstanceManager → BLAS/TLAS`. O relatório desta validação offline não declara que essa integração já ocorreu; os logs e o teste de implementação deverão registrar as etapas realmente aceitas. O gate de validação em [validation.json](./validation.json) permite apenas implementar e testar essa candidata.

## Artefatos e reprodução

- [OBJ com posição, UV e normal decodificadas](./selected_object.obj).
- [Visualização offline](./geometry_uv_projection.png), sem textura e sem pixels raster originais.
- [Validação completa dos três frames](./validation.json).
- [Ranking das candidatas](./candidate_ranking.json).
- [Manifesto de replay](./replay_manifest.json), bytecodes originais, cinco buffers usados e bancos de constantes VS/PS F/I/B por frame em `replay/`.
- [VS desassemblado](./f098f457d9ae603bea5f84ec985d473c29cfb0c9.dxso.dis) e [PS desassemblado](./44b92a29eb3e5f2ee6d53f02e14fb11a01511da8.dxso.dis).

Comandos em PowerShell:

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
& '.\.tools\build-python\Scripts\python.exe' '.\analysis\edf\new_candidate\audit_windows.py'
& '.\.tools\build-python\Scripts\python.exe' '.\analysis\edf\new_candidate\select_and_validate.py'
& '.\runtime\_build\edf_projection.exe' '.\analysis\edf\new_candidate\runtime_projection_input.bin'
```

Para replay com os bytes originais usados, o manifesto explica como preencher os primeiros 630 vértices com padding e vincular os índices originais 630…881. Buffers temporários do replay e texturas sintéticas não mudam o escopo da whitelist nem substituem a validação visual no jogo.

## Resultado posterior da implementação

O protótipo restrito foi compilado e passou no replay controlado x64 e x86 via Bridge com os dados desta candidata. O caminho de CameraManager, SceneManager, InstanceManager, BLAS e TLAS opaca foi observado, com identidade estável, controle negativo rejeitado e raster byte a byte preservado. A prova usa texturas sintéticas; o teste dentro do EDF continua pendente. Veja [IMPLEMENTATION.md](./IMPLEMENTATION.md), os logs finais e [a validação consolidada](../next_validation/validation.json). Esta seção não transforma os resultados offline anteriores em uma confirmação visual no jogo.
