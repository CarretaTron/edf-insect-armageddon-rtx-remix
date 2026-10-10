# Auditoria independente de shader/layout — EDF: Insect Armageddon

Revisão somente leitura das capturas reais, sem alteração de código, instalação ou execução do jogo. Código-base: `0e18e55c04309c08e6206e768bc72b24ac83f853+compat-capture`. Os arquivos neste diretório são derivados da captura; não constituem um perfil de produção.

## Candidata e evidências verificadas

Captura `captures/edf/1791384430306-28796-6`, frame **7572**, draw **284**, `DrawIndexedPrimitive`, triangle list, 84 vértices e 56 triângulos. A captura é completa. A candidata usa shaders SM3:

| Identidade | SHA-1 do conteúdo original |
|---|---|
| VS | `f098f457d9ae603bea5f84ec985d473c29cfb0c9` |
| PS | `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8` |
| Declaração | `6ff4217f660b280ef21cd86f83889aa93219aaa6` |
| Índices usados | `8b4a8e527b38456f60762c5abf062efc37a2f6a3` |

`candidate_audit.py` verifica tamanho e SHA-1 dos blobs, finitude dos atributos decodificados, 168 índices de 16 bits, intervalo `[0,83]` e utilização dos 84 vértices. `candidate_audit.json` preserva as referências de origem, os estados e os resultados. `capture_inspect.py` consolida as dez capturas, incluindo prefixos truncados: 7.907 draws e 95 identidades únicas `(stage, hash)`. Frequências de draws de capturas truncadas não representam frames completos.

### Layout e valores

| Stream | Declaração | Registro VS | Stride | Decodificação demonstrada pelo VS |
|---|---|---|---|---|
| 0 | BINORMAL0, UBYTE4 | v0 | 4 | `xyz * c9.z - c9.w` |
| 1 | NORMAL0, UBYTE4 | v1 | 4 | `xyz * c9.z - c9.w` |
| 2 | POSITION0, SHORT4 | v2 | 8 | `xyz * c9.x` |
| 3 | TANGENT0, UBYTE4 | v3 | 4 | `xyz * c9.z - c9.w` |
| 4 | TEXCOORD0, SHORT2 | v4 | 4 | `xy * c9.y` |

Todos os streams usados têm frequency 1. Streams 5 e 6 permanecem vinculados, mas não aparecem nesta declaração nem nos inputs do VS; são estado residual e não devem participar do decoder desta draw.

`c9 = (1/1024, 1/2048, ~1/127, 1)`. SHORT4 e SHORT2 são inteiros **com sinal**, não formatos normalizados. UBYTE4 é convertido pelo input assembler para floats 0..255. Isso está demonstrado em `src/d3d9/d3d9_util.cpp:298` (`DecodeDecltype`), `src/d3d9/d3d9_device.cpp:6885` (`BindInputLayout`) e `src/dxso/dxso_compiler.cpp:3638` (`emitInputSetup`). O VS usa apenas componentes realmente presentes: position.xyz, UV.xy e normal/tangent/binormal.xyz. Os componentes faltantes/padrões da declaração não influenciam essas fórmulas; position.w é ignorado e o ponto homogêneo é construído somando c3.

Bounds locais decodificados: X `[-9.94921875, 9.951171875]`, Y `[-4.580078125, 6.2138671875]`, Z `[0, 24.845703125]`. UV: U `[0.0068359375, 0.9931640625]`, V `[0.00634765625, 0.9931640625]`. Comprimentos das normais decodificadas `[0.99506, 1.00374]`, compatíveis com quantização. Esses resultados validam a leitura e as escalas; não validam ainda câmera, posição mundial ou nome do objeto.

### Fluxo de posição, UV e normal

Tomando `p = SHORT4.xyz * c9.x`, as instruções produzem literalmente:

```text
clip = p.x*c0 + p.y*c1 + p.z*c2 + c3
UV   = SHORT2.xy*c9.y
TEXCOORD0 = ((clip.z-c8.x)*c8.y, UV.x, UV.y)

nLocal = UBYTE4.xyz*c9.z-c9.w
nOutput = nLocal.x*c4.xyz+nLocal.y*c5.xyz+nLocal.z*c6.xyz
TEXCOORD3 = nOutput

TEXCOORD4 = (p.x-c7.x)*c4.xyz
          + (p.y-c7.y)*c5.xyz
          + (p.z-c7.z)*c6.xyz
```

O CTAB nomeia c0..c3 como `LocalToProjection`, c4..c6 como `LocalToWorld`, c7 como `veCameraLocal`, c8 como fog e c9 como escalas de fixed point. Esses nomes corroboram as operações, mas não resolvem uma decomposição completa W/V/P. c4..c6 contêm uma transformação linear 3×3, sem informação de translação. A análise matemática complementar precisa validar uma decomposição compatível, sua consistência entre draws/frames e sua concordância com a projeção raster.

Este VS não contém blend indices/weights, endereçamento relativo, loops ou skinning. Isso prova um caminho sem skinning neste shader; uma entidade rígida móvel também poderia usá-lo. A classificação segura é **candidata opaca sem skinning**, até a validação de estabilidade entre frames.

## PS: textura base, alpha e passe MRT

O PS amostra a textura 2D s0 com `texld r0, v0.yzzw, s0`. Portanto seus UVs são **TEXCOORD0.yz**. O primeiro slot tem textura DXT1 128×128, resource 5078, 8 mips; `D3DSAMP_SRGBTEXTURE=0`. s1/s2 são cubemaps DXT1, de 64² e 256², usados com normal e direção de reflexão. As outras texturas vinculadas não são samplers utilizados por este PS.

O alpha de s0 participa de mistura/intensidade material, não de transparência. A expressão de cor difusa anterior à iluminação é `sample.rgb * ((1-sample.a)*c1.rgb + sample.a)`. Nesta draw, c1 é `(1,1,1,1)`, portanto essa expressão é exatamente `sample.rgb`. O PS não usa `texkill`, escreve `oC0.w=1`, e os estados têm alpha test e blend desligados, Z write ligado e `D3DCULL_CW=2` (descarta triângulos no sentido horário). O protótipo pode usar s0 como base RGB opaca com alpha ignorado, sem afirmar que o restante do material é PBR fisicamente equivalente.

O shader escreve três MRTs:

| Saída | Operações observadas |
|---|---|
| oC0 | Cor com iluminação solar c2/c3, termo especular e alpha 1 |
| oC1 | Cubemap de ambiente/reflexão; alpha recebe fog de TEXCOORD0.x |
| oC2 | Normal codificada em x/y/w como `0.5*n+0.5`; z recebe termo especular |

Os três RTs (resources 4831/4832/4833) são A8R8G8B8 1920×1080; depth é D24S8, resource 628. Nenhum RT é o backbuffer. **Isso não implica rejeição automática pelo Remix:** `src/d3d9/d3d9_rtx_utils.cpp:35`, `isRenderTargetPrimary`, compara somente width/height com as dimensões do backbuffer. Um passe R32F de profundidade com as mesmas dimensões também pode passar por esse critério. O perfil deve distinguir passe por shader/layout/formato/estado, sem depender apenas de dimensões ou de `backBuffer`.

Essas saídas demonstram um passe de material com dados para processamento posterior. Ainda falta identificar a draw que consome esses RTs, o último composite e o instante correto da injeção RTX. Descartar a draw original porque sua geometria entrou no RTX poderia deixar RTs necessários ao jogo incompletos. Preservar o raster original é necessário no primeiro experimento.

## Incompatibilidades concretas no caminho genérico

### 1. Transformações FFP identidade

`D3D9Rtx::processRenderState`, `src/d3d9/d3d9_rtx.cpp:340`, lê WORLD/VIEW/PROJECTION de `SetTransform`. Todas são identidade na candidata. A posição final do VS depende de c0..c3. `prepareVertexCapture:165` usa inversas das transforms do `DrawCallState`; sem overrides comprovados, a posição capturada permanece incorretamente interpretada a partir de clip space.

### 2. UV sai como fog/u

`processVertices:246` conserva SHORT2 sem aplicar c9.y. `RtxGeometryUtils::isTexcoordFormatValid`, `src/dxvk/rtx_render/rtx_geometry_utils.h:67`, rejeita esse formato. `prepareVertexCapture:197` então seleciona saída TEXCOORD0 mesmo quando `useVertexCapturedTexcoords=false`. `DxsoCompiler::emitVertexCaptureOp`, `src/dxso/dxso_compiler.cpp:3814`, captura somente TEXCOORD0.xy. Para este shader isso significa `(fog,u)`, enquanto o PS usa `(u,v)` de yz. Apenas corrigir as matrizes não corrige material/UV.

### 3. Normal inteira não decodificada

`emitInputSetup:3705` associa o input NORMAL0 a `m_vs.oNormal0`. Não há output com semântica NORMAL0 neste VS: a normal transformada sai em TEXCOORD3. O trecho de `emitVertexCaptureOp:3827` lê o NORMAL0 do input assembler e aplica a transformação, mas não aplica `*c9.z-c9.w`. Assim, o caminho genérico captura aproximadamente `(0..255)` em vez de normais assinadas. Normalizar esses números posteriormente não recupera as direções corretas.

### 4. AABB é calculada antes da vertex capture

Em `D3D9Rtx::internalPrepareDraw`, `src/d3d9/d3d9_rtx.cpp:723`, a ordem é `processVertices -> computeHash -> computeAxisAlignedBoundingBox -> processSkinning -> prepareVertexCapture`. `computeAxisAlignedBoundingBox`, `src/d3d9/d3d9_rtx_geometry.cpp:220`, interpreta o position buffer como `Vector3` float, sem conversão baseada em formato. Se a opção que requer bounding box estiver ativa, SHORT4 stride 8 vira floats inválidos e a leitura de 12 bytes por vértice ultrapassa o intervalo lógico de 8 bytes do último vértice. O impacto físico depende do tamanho do buffer subjacente; o AABB já é incorreto independentemente disso.

### 5. Formatos compactados não podem ser simplesmente enviados ao interleaver

`RtxGeometryUtils::interleaveGeometry`, `src/dxvk/rtx_render/rtx_geometry_utils.cpp:847`, consulta `formatConversionFloatSupported`. A lista em `src/dxvk/shaders/rtx/pass/interleave_geometry.h:55` não inclui SHORT4 SSCALED, SHORT2 SSCALED ou UBYTE4 USCALED. Logo, desligar vertex capture e enviar os buffers compactados intactos não constitui uma solução.

### 6. Slots de textura não descrevem genericamente o material PS

`processTextures<false>`, `src/d3d9/d3d9_rtx.cpp:935`, escolhe os dois primeiros slots, e `LegacyMaterialData::as<OpaqueMaterialData>`, `src/dxvk/rtx_render/rtx_materials.cpp:149`, pode copiar a segunda textura para `SecondaryTexture`. Aqui s1 é um cubemap de iluminação, não a segunda textura difusa. O perfil deve selecionar s0, preservar sampler/sRGB, ignorar alpha como opacidade e não transferir s1 para um material legado como se representasse essa mesma semântica.

## Integração mínima recomendada, sem alteração nesta entrega

Não há necessidade demonstrada de modificar o compilador genérico SM3. Uma regra restrita a executável/fingerprint, VS/PS por SHA-1 de bytecode, declaração compatível, frequency 1, passe MRT e estados opacos pode usar um `EdfDrawCallDecoder` separado.

1. Depois de `processRenderState`, aplicar transforms comprovadas somente ao `DrawCallState`; não alterar `SetTransform` nem os estados D3D9 do jogo. Os filtros/classificação da draw podem precisar do resultado do perfil antes da preparação final, mas não devem adivinhar shaders desconhecidos.
2. Depois de `processVertices` e **antes de `computeHash`/AABB**, substituir posições por float3/float4 com a escala c9.x, UV por float2 com c9.y e normais por float3 com c9.z/w. Usar ranges já derivados/rebased, offsets e staging/lifetime existentes. Não reconstruir ranges a partir de ponteiros temporários e não incluir streams residuais 5/6.
3. Para somente esse resultado comprovadamente decodificado, evitar `prepareVertexCapture`, que sobrescreveria a posição/normais/UV com o caminho genérico inadequado. Manter `rtx.useVertexCapture` global como está: `makeDrawCallType:434` rejeita shaders programáveis quando a opção global está desligada. Usar uma condição por draw com origem/estado explicitamente representados.
4. Aplicar a regra de material após `processTextures`, antes de `materialData.updateCachedHash`: s0/sampler/sRGB, secundária vazia, base opaca e alpha ignorado. O ponto concreto do ignore-alpha depende de como a política se materializará em `OpaqueMaterialData`; não assumir que um render state opaco sozinho modifica o alpha da textura.
5. Separar `preserveOriginalDraw` de `needVertexCapture` para a draw decodificada experimental. Hoje `src/d3d9/d3d9_rtx.cpp:774` define a preservação somente pela necessidade de vertex capture. Desligar capture por draw sem preservar o raster quebraria a atualização dos MRTs.
6. Verificar identidade: `computeHash`, `src/d3d9/d3d9_rtx_geometry.cpp:159`, ainda inclui bytecode/constantes VS quando a opção global de capture está ligada e a regra de GeometryDescriptor está habilitada. c0..c3 mudam com a câmera. O hash completo pode variar; hash de asset usado por replacements e cache/instâncias precisam ser avaliados separadamente. Não trocar hashes globalmente para esconder essa variação.
7. Reutilizar `RasterGeometry`, índices, `SceneManager`, `InstanceManager`, BLAS/TLAS e renderer. Evitar categorias sky/terrain especiais neste primeiro objeto, até que haja evidência para elas.

## Informação ainda faltante

| Informação | Onde deveria estar / como obter | Por que necessária |
|---|---|---|
| W/V/P separáveis e estáveis | c0..c7 + dados de outros draws/frames; validação matemática complementar e movimento controlado | Clip correto não determina sozinho uma câmera mundial única |
| Silhueta raster e saída real VS | Frame raster original, wireframe/debug ou replay de shader validado | Confirma projeção, handedness, viewport e escolha do objeto |
| Conteúdo/identidade da textura | Uploads CPU/hash interno `DxvkImage`, ou readback controlado | Os atuais descriptorHash/resource ID não identificam pixels; M5 requer comprovação visual |
| Composite e timing RTX | Ordem de RTs/texturas/draws e pixel shaders do final do frame | MRT preservado e cena RTX correta não garantem que o frame final exibirá o path tracing |
| Estabilidade da geometria rígida | Mesmos hashes/ranges/regra em frames diferentes e câmera movida | Sem skinning não significa cenário estático |

Os blobs atuais bastam para decodificar exatamente posições, índices, UV e normais desta candidata. O log informa `textureContentCaptured=false`; não há pixels raster nem readback dos RTs. O resultado registrado `raytracing_or_draw_conversion_disabled` é compatível com a captura de observação: esta auditoria não demonstra ainda criação de uma mesh RTX, câmera correta ou path tracing.
