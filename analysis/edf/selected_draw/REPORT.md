# EDF: validação de uma draw estática e opaca

Os dados reais sustentam um **protótipo restrito a uma mesh**. Posição, índices, UV e fluxo do vertex shader foram comprovados nos buffers capturados. A câmera admite uma fatorização matematicamente consistente entre draws e frames, em uma origem escolhida e documentada. Ainda não houve reconstrução desta mesh dentro do RTX nem comparação com pixels do raster original.

Foram encontradas dez janelas, totalizando 7.907 draws e 29.450 blobs. Os hashes/tamanhos dos dados escritos são válidos. As janelas -6/-7 são completas de gameplay; -0/-1 têm apenas três draws; -2…-5/-8/-9 são prefixos interrompidos por `queue_limit`. Como não há associação confiável entre arquivos e intenção de mover/parar, as comparações abaixo usam os valores efetivos. Não há um terceiro frame positivo da mesh escolhida.

Evidência completa: [auditoria entre capturas](<D:/EDF IA RTX REMIX/analysis/edf/cross_capture/findings.md>), [auditoria independente dos shaders](<D:/EDF IA RTX REMIX/analysis/edf/shader_review/report.md>) e [resultados numéricos reproduzíveis](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/validation.json>).

## 1. Candidata e identidade

| Captura | Frame | Draw, zero-based | Estado |
|---|---:|---:|---|
| `1791384430306-28796-6` | 7572 | 284 | Janela completa |
| `1791384433361-28796-7` | 7644 | 281 | Janela completa |

Ambas executam `DrawIndexedPrimitive(TRIANGLELIST, BaseVertexIndex=0, MinVertexIndex=0, NumVertices=84, StartIndex=4728, PrimitiveCount=56)`. Existem 168 índices UINT16, de 0 a 83, utilizando todos os 84 vértices. Os intervalos de índices e de todos os atributos da declaração passam pela validação de bounds.

| Recurso | SHA1 por conteúdo |
|---|---|
| VS SM3 | `f098f457d9ae603bea5f84ec985d473c29cfb0c9` |
| PS SM3 | `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8` |
| Declaração serializada | `6ff4217f660b280ef21cd86f83889aa93219aaa6` |
| POSITION usado | `c494580e5b4cd375e416dcd0c807a23344ac3c74` |
| UV usado | `132df6203bfddc1c41fbee3e81a43645d04f8d66` |
| Índices usados | `8b4a8e527b38456f60762c5abf062efc37a2f6a3` |

Os cinco atributos, índices, escala de posição/UV e base linear de transformação são idênticos nos dois frames. O VS não possui skinning, branches, loops, endereçamento relativo ou deformação da posição. A variação espacial concorda com a câmera observada em outros conjuntos de transformação. Isso sustenta **geometria estática nos frames analisados**, sem estabelecer nome da entidade, permanência durante toda a missão ou imunidade a destruição.

`ALPHABLENDENABLE=0`, `ALPHATESTENABLE=0`, `ZENABLE=1`, `ZWRITEENABLE=1`, color-write RGBA e `CULLMODE=2` (`D3DCULL_CW`, descarta CW). O PS não executa `texkill` e escreve alpha 1 em oC0. Portanto a draw é opaca nesse passe.

## 2. Layout demonstrado

| Stream | Uso / registro VS | Tipo | Offset de binding | Stride | Operação no shader |
|---:|---|---|---:|---:|---|
| 0 | BINORMAL0 / v0 | UBYTE4 | 8988 | 4 | xyz × c9.z − c9.w |
| 1 | NORMAL0 / v1 | UBYTE4 | 8988 | 4 | xyz × c9.z − c9.w |
| 2 | POSITION0 / v2 | SHORT4 | 17976 | 8 | xyz × c9.x |
| 3 | TANGENT0 / v3 | UBYTE4 | 8988 | 4 | xyz × c9.z − c9.w |
| 4 | TEXCOORD0 / v4 | SHORT2 | 8988 | 4 | xy × c9.y |

Os elementos têm offset zero dentro de cada stream e frequency 1. Streams 5/6 vinculados não pertencem à declaração usada por este VS. SHORT4/SHORT2 fornecem inteiros com sinal convertidos para float, sem normalização automática; SHORT2 expande z/w para 0/1. UBYTE4 fornece bytes unsigned crus. Essas conversões conferem com [D3DDECLTYPE, Microsoft](https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3ddecltype) e `DecodeDecltype` do Runtime.

Os blobs de posição e UV começam exatamente em seus offsets de binding: 672 bytes de posição e 336 de UV. O blob IB começa em 9456 = 4728 × 2, tem 336 bytes e conserva os índices originais. Não existe necessidade de adivinhar interleaving ou rebasing nesta draw.

`c9=(1/1024,1/2048,float32(1/127),1)`. POSITION.w não entra na transformação; o termo homogêneo vem de c3. Bounds locais após decodificação:

```text
X: [-9.94921875,  9.951171875]
Y: [-4.580078125, 6.2138671875]
Z: [0,           24.845703125]
U: [0.0068359375, 0.9931640625]
V: [0.00634765625,0.9931640625]
```

As normais decodificadas têm comprimento entre 0,99506 e 1,00374, coerente com a quantização. Não é necessário usar esses vetores para provar a posição, mas eles corroboram o layout.

## 3. Prova do cálculo de posição e UV

[VS desassemblado](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/f098f457d9ae603bea5f84ec985d473c29cfb0c9.dxso.dis>) e [PS desassemblado](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/44b92a29eb3e5f2ee6d53f02e14fb11a01511da8.dxso.dis>) preservam os símbolos do compilador do jogo. Os nomes corroboram as instruções; não foram usados sozinhos para classificar matrizes.

A cadeia de posição é explícita:

```text
mul r3.xyz, c9.x, v2
mul r0, r3.y, c1
mad r0, r3.x, c0, r0
mad r0, r3.z, c2, r0
add r0, r0, c3
mov o0, r0

p    = SHORT4.xyz * c9.x
clip = p.x*c0 + p.y*c1 + p.z*c2 + c3
```

Portanto c0…c3 são a transformação **local → clip** nesta draw. Não são matrizes World, View e Projection separadas.

O UV é escrito com `mul o1.yz, c9.y, v4.xxyw`. O componente x de o1 recebe `(clip.z-c8.x)*c8.y`, usado como fog. O PS declara TEXCOORD0 em v0 e faz `texld r0, v0.yzzw, s0`, usando **y/z** como coordenadas da textura 2D. Logo:

```text
TEXCOORD0 = (fog, U, V)
UV        = SHORT2.xy / 2048
```

Uma captura genérica de TEXCOORD0.xy forneceria `(fog,U)`.

`validate_edf_draw.py` executa as 26 instruções aritméticas do VS para cada um dos 84 vértices, interpretando máscaras, swizzles e negação. Compara o resultado executado com as equações de posição, UV e normal. **Erro máximo: zero na aritmética double deste teste**, nos dois frames. Isso comprova a álgebra e o fluxo de dados, sem equivaler a uma medição bit-exact de FMA/GPU ou comparação com raster.

## 4. Transformação do objeto e câmera

Além da posição, o shader produz TEXCOORD4 = `(p-c7.xyz)*B`, onde B é a matriz 3×3 cujas linhas são c4.xyz, c5.xyz e c6.xyz. TEXCOORD3 contém a normal local decodificada multiplicada por B. Os símbolos indicam `LocalToWorld` e `CameraLocalPosition`, coerentes com essas operações.

Tomando A como as três primeiras linhas de local→clip, testamos:

```text
M = inverse(B) * A
k = c7.xyz * A + c3
clip = (p - c7.xyz) * B * M + k
```

A identidade algébrica sozinha não é validação. O teste independente é que **M concorda entre 74 draws do frame 7572 e entre 70 draws do frame 7644**, com variação máxima por elemento de 1,70e-7 e 1,64e-7, apesar das diferentes bases B e posições locais de câmera. k é próximo de `(0,0,-0.25,0)`.

As colunas x/y/w de M são ortogonais, a coluna w tem comprimento próximo de 1 e z é proporcional a w. Podemos escolher uma fatorização perspectiva RH com rotação própria, usando:

```text
fx = ||M.colX||           ~1.732045
fy = ||M.colY||           ~3.079192
a  = dot(colZ,colW)/dot(colW,colW) ~1.00006102036
b  = k.z                 ~-0.25

R.colX = M.colX/fx
R.colY = M.colY/fy
R.colZ = -M.colW
P = [[fx,0,0,0], [0,fy,0,0], [0,0,-a,-1], [0,0,b,0]]
```

Essa forma de P corresponde à [projeção perspectiva RH documentada pela Microsoft](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/bb281728(v=vs.85)). É uma fatorização operacional validada, não prova de que o jogo tenha chamado essa função.

No protótipo de **um objeto**, podemos fixar sua translação em zero e conservar B como base de objeto. A câmera nessa origem é `c7*B`; a translação de V é `-(c7*B)*R`. W permanece fixa e W×V×P reproduz os valores local→clip capturados. A rotação tem determinante próximo de +1 e erro de ortogonalidade inferior a 1e-7. Para todos os vértices, o maior erro da projeção fatorada em relação ao clip calculado pelo VS é **0,000719 pixel** em viewport 1920×1080.

O [teste C++ com os helpers reais do Remix](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/verify_runtime_projection.cpp>) também passou. Ele constrói `Matrix4`, compõe **P×V×W** na convenção interna de vetores-coluna e chama `decomposeProjection` de `rtx_matrix_helpers.h`. Os dois resultados são RH, sem reverse-Z, shear zero, FOV vertical 0,628031 rad e aspecto 1,77778. O maior erro float32 de projeção frente à matriz original é 0,000697 pixel. [Resultado do teste](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/runtime_projection_verification.json>). Isso valida convenção de armazenamento e o helper de projeção, sem afirmar que CameraManager/SceneManager ou o GPU já receberam essa draw.

FOV horizontal ≈60°, vertical ≈35,9835°, aspecto ≈16/9 e near estimado ≈0,25. Far derivado fica próximo de 4,1 mil unidades, mas a divisão por `a-1` amplifica arredondamento; não se deve codificar um far exato a partir dessa estimativa.

Entre os frames, c4…c6/c8/c9 são idênticos; c0…c3/c7 mudam. A candidata fornece deslocamento relativo de câmera `[-0.683024222,-0.269897897,-0.002225876]`. Dez pares distintos de transforms de meshes recorrentes concordam com esse movimento, com residual máximo 3,75e-5. Esse consenso corrobora câmera variável e objeto rígido estável nos frames observados.

**Tradução World absoluta não foi recuperada.** Os .w de c4…c6 não são usados para translação. A origem escolhida acima é suficiente para o objeto isolado e deve permanecer fixa entre frames. Não deve ser confundida com o sistema global original do EDF. As três matrizes de SetTransform desta draw são identidade e não fornecem essa informação.

## 5. Material e passes

s0 é uma textura 2D DXT1 128×128, oito mips, resource de sessão 5078. O shader usa seu RGB na cor de superfície; s1/s2 são cubemaps de iluminação/reflexão. A cor difusa anterior à iluminação é `sample.rgb * ((1-sample.a)*PS_c1.rgb + sample.a)`. PS_c1 é branco nesta candidata, portanto o fator reduz para 1. O alpha da textura é dado material, não opacidade da draw. O primeiro protótipo pode escolher RGB de s0 como base e ignorar alpha/cubemaps, preservando sampler e sRGB=0.

São escritos MRTs 4831/4832/4833, A8R8G8B8 1920×1080, depth D24S8 resource628. A mesma posição/IB aparece nas draws56 dos dois frames com outro par de shaders, que exporta z/w num RT R32F; não deve virar uma segunda instance por duplicação de passe.

[pass_chain.json](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/pass_chain.json>) registra consumidores diretos dos MRTs: draw630/646 lê RT4833; draw633/649 lê os três MRTs nos samplers5/6/7 e o RT4818 no sampler8, escreve no backbuffer627 e usa PS `f6ec35294efe3e2709d71ec792c3d46a54dc16ea`. Existem passes posteriores, então isso não identifica sozinho o ponto final de injeção RTX.

O filtro `isRenderTargetPrimary` do Remix compara dimensões; offscreen não significa rejeição automática. Preservar o raster desta draw é necessário para manter os dados que os consumidores usam.

## 6. Menor decoder proposto

Uma classe isolada `EdfStaticDrawDecoder`, selecionada por executável/fingerprint e desligada por padrão, receberia estado efetivo e ranges já validados. A primeira regra deve exigir **este par SHA1, declaração, estados opacos, frequency1 e conteúdo desta mesh**; não números de draw nem endereços COM. Shaders/meshes desconhecidos continuam pelo comportamento anterior.

1. Decodificar POSITION/UV/NORMAL para buffers próprios float3/float2/float3, usando c9 efetivo. Reutilizar índices UINT16 e lifetime/staging do Runtime.
2. Aplicar ao DrawCallState a origem fixa de objeto, V/P fatoradas e textura s0, com os guards numéricos documentados. Não escrever transforms no device D3D9 do jogo.
3. Instalar a geometria **antes de computeHash/AABB** em `D3D9Rtx::internalPrepareDraw`, e evitar `prepareVertexCapture` só para a draw resolvida. O caminho genérico pegaria UV `(fog,U)`, normais UBYTE4 sem remapeamento e AABB a partir de SHORT4 interpretado como floats.
4. Separar `preserveOriginalDraw` de `needVertexCapture`: manter o raster MRT desta draw no protótipo. Não desligar vertex capture globalmente, pois o classificador rejeita VS programáveis nessa condição.
5. Usar a identidade dos buffers decodificados para geometria e manter câmera/instance transforms separados. O hash completo atual pode incluir constantes VS que mudam com a câmera; resolver isso por draw decodificada, conservando as regras dos outros jogos.
6. Entregar RasterGeometry/DrawCallState ao caminho existente: SceneManager → InstanceManager → BLAS/TLAS. Não há evidência de necessidade de ShaderReplacement, mudança da API pública Remix ou reescrita desses subsistemas.

Arquivos previstos para esse incremento posterior: `src/d3d9/d3d9_game_compatibility.{h,cpp}`, integração curta em `d3d9_rtx.{h,cpp}`, condição de hash em `d3d9_rtx_geometry.cpp` e registro Meson. **O decoder não foi implementado nesta etapa.**

## 7. Limites, próximo experimento e reprodução

Os blobs existentes bastam para a comprovação offline de layout e shader. Para validar o recebimento RTX e subir os milestones, ainda faltam:

| Dado/teste | Origem necessária | Consequência |
|---|---|---|
| Terceiro frame positivo da mesma mesh | F10 com ela ainda visível, janela completa e filtrada | Separar repetição imóvel e movimento controlado sem inferir intenção |
| Silhueta sobre raster original | Imagem do mesmo frame ou overlay diagnóstico | Medir concordância GPU/raster/culling e reconhecer a entidade |
| Pixels da textura base | Upload/readback selecionado ou captura GPU | Validar aparência/orientação, conteúdo e material em M5 |
| Cena/instance RTX de fato | Decoder restrito + debug de uma instance | M3/M4 dependem da validação no renderer |
| Timing de path tracing | Fluxo do composite e experimento RTX isolado | Evitar sobrescrita posterior ou MRTs incompletos |

Essas lacunas não autorizam atribuir W/V/P ou materiais a outros shaders. A tradução global não é bloqueio para o experimento de uma mesh ancorada; é uma limitação para a expansão sem uma referência persistente.

A validação offline é reproduzível:

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
& 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe' `
  .\tools\edf_capture\validate_edf_draw.py 'D:\EDF IA RTX REMIX\captures\edf' `
  --output 'D:\EDF IA RTX REMIX\analysis\edf\selected_draw'
& 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe' `
  -m unittest discover -s .\tools\edf_capture\tests -p test_validate_edf_draw.py -v
```

Os sete testes passam: formatos signed/raw, limites, instancing, swizzle UV/fog, leitura não inicializada, opcode desconhecido, fatorização conhecida, transposição incorreta e câmera incompatível. O JSON de validação registra duas correspondências, nenhuma falha de ranges e erros de posição/UV/normal zero no cálculo offline.

O probe C++ pode ser recompilado e executado com `& 'D:\EDF IA RTX REMIX\analysis\edf\selected_draw\verify_runtime_projection.ps1'`. Ele usa o MSVC instalado em `D:\EDFBuildTools` e os headers locais do Remix/USD, e grava `runtime_projection_verification.json`.

[OBJ decodificado](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/selected_object.obj>) contém posições locais, UV e normais. Não contém textura nem uma tradução global adivinhada.

![Mesh, UV e projeção calculada](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/geometry_uv_projection.png>)

A imagem é derivada dos bytes e shaders capturados. Ela não apresenta pixels raster originais. M1/M2 têm evidência operacional nesta execução; M3…M7 ainda não estão demonstrados dentro do RTX.

## 8. Instrumentação adicional implementada

**Problema:** seis janelas reais foram interrompidas por `queue_limit`; o orçamento total de 256 MiB não esgotou. A fila assíncrona estava fixa em 16 MiB. Isso impede usar a ausência de draws nos sufixos truncados como evidência e deixa sem uma terceira observação positiva da candidata.

**Correção restrita:** `rtx.compatCapture.maxQueueMiB` configura a fila, com padrão 16 e faixa 1…256. O perfil [edf-static-observe.conf](<D:/EDF IA RTX REMIX/runtime/tools/edf_capture/edf-static-observe.conf>) usa 64 MiB, captura um frame por F10 e filtra somente o par de shaders validado. O footer registra `max_queue_bytes` e `peak_queue_bytes`; a carga inclui o item em escrita, cujo payload é liberado antes de devolver o orçamento. O limite de disco continua independente. Isso não altera a interpretação das draws nem o renderer.

Arquivos de produção deste incremento: `src/util/util_compat_capture.{h,cpp}`, `src/dxvk/rtx_render/rtx_compat_capture.{h,cpp}` e documentação `RtxOptions.md`. Os testes do writer foram ampliados no harness existente. Um blob de 20 MiB é recusado na fila padrão e aceito/deduplicado/drenado na fila de 64 MiB; os testes também confirmam orçamento de disco, hash/tamanho, concorrência e erro de I/O. Onze footers independentes têm pico dentro do limite. [Resultados do writer](<D:/EDF IA RTX REMIX/analysis/edf/shader_review/queue-writer-verification.json>).

A compilação `meson compile -C _CompDebugOptimized_x64 -j2 d3d9` terminou com exit 0. A DLL tem SHA256 `36559e90e8d12626ec56f061971180e3f4eba53c30d5e21fa23b74341596fcfa`. A documentação foi regenerada pelo Runtime. [Relatório da mudança instrumental](<D:/EDF IA RTX REMIX/analysis/edf/shader_review/queue-limit-report.md>).

Os probes controlados **x64 e x86/Bridge** terminaram com exit 0 e passaram em **1.584 verificações cada**, com 24 draws em três frames, `window_complete`, nenhum erro/truncamento e fila efetiva de 64 MiB. Picos: 107.925 bytes x64 e 87.929 bytes x86. [Resultado x64](<D:/EDF IA RTX REMIX/analysis/edf/shader_review/queue64-probe-x64-verification.json>) e [resultado x86](<D:/EDF IA RTX REMIX/analysis/edf/shader_review/queue64-probe-x86-verification.json>).

A execução inicial restrita encontrou crash de servidor x86 (`0xc00000fd` em CreateDevice) e um x64 que concluiu a captura, mas não encerrou. Os logs foram preservados, os processos controlados encerrados e os mesmos probes repetidos sem o token restrito; ambas as repetições passaram e encerraram normalmente. Isso delimita os resultados de teste, sem estabelecer a causa exata das falhas iniciais ou atribuí-las ao EDF. Não foi necessário mudar o renderer para as repetições.

O pacote novo está em `D:\EDF IA RTX REMIX\packages\queue64\edf-diagnostics-x86`, separado do anterior. A [verificação dos manifests](<D:/EDF IA RTX REMIX/packages/queue64/package-verification.json>) conferiu os 187 arquivos x64 e 189 arquivos x86/Bridge, incluindo a identidade da DLL compilada. O Bridge não precisou de mudança neste incremento. Nenhum arquivo da instalação do EDF foi alterado por esta execução.

Siga [NEXT_CAPTURE.md](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/NEXT_CAPTURE.md>) para trocar o pacote, capturar sem/mover a câmera e executar a comparação. A recaptura é o teste do limite maior na carga real do EDF; os testes controlados não substituem essa evidência.
