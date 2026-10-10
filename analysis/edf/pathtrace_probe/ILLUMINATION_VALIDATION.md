# Validação da iluminação do probe — A/B x64

O buffer `direct_diffuse_radiance` vazio no primeiro teste era consequência da direção da luz de diagnóstico. Inverter exclusivamente `rtx.fallbackLightDirection` fez a GPU produzir radiância direta em todos os pixels atingidos pela mesh nos três frames. O Runtime e o executável de replay são idênticos nos dois testes. Nenhuma correção de shader ou decoder foi necessária.

O resultado vale para o replay controlado da única mesh EDF validada, com 252 vértices, 432 índices e 144 triângulos. Não demonstra recuperação da iluminação original do EDF nem autoriza composição visual no jogo.

## Evidência GPU

| Frame GPU | Hits de geometria | Direta não zero, direção original | Direta não zero, direção invertida | Targets originais idênticos antes/depois |
| --- | ---: | ---: | ---: | --- |
| 0 | 6435 | 0 | 6435 | 4 de 4 |
| 1 | 6438 | 0 | 6438 | 4 de 4 |
| 2 | 6895 | 0 | 6895 | 4 de 4 |

Em cada frame iluminado, os pixels RGB não zero do buffer direto coincidem com todos os hits de geometria. Não há valores não finitos. `gpuCompleted=true`, `surfaceCount=1`, `lightCount=1`, uma instance na whitelist, câmera atualizada no próprio frame e `compositionPerformed=false` constam dos metadados e do log. A análise também refez a comparação integral dos quatro pares de targets.

As matrizes de câmera são iguais entre os casos escuro/iluminado. Os buffers `linear_z` e `albedo` são iguais byte a byte entre os dois casos, em cada frame. Portanto a alteração de radiância não decorre de uma mudança de câmera, geometria ou albedo.

## Convenção da luz e cálculo independente

`LightManager::prepareSceneData`, em `runtime/src/dxvk/rtx_render/rtx_light_manager.cpp:270`, normaliza a direção configurada para criar a luz distante; na linha 271 divide o ângulo completo por dois. `distantLightSampleArea`, em `runtime/src/dxvk/shaders/rtx/concept/light/distant_light.slangh:78`, posiciona a amostra em `position - distantDirectionSample * 100000`. Logo, a direção configurada representa o percurso da luz; a direção da superfície para a luz tem sinal oposto.

- Configuração escura: direção de percurso `(-0.2, -1, +0.4)`; direção central da superfície para a luz `(+0.182574, +0.912871, -0.365148)`.
- Configuração iluminada: direção de percurso `(+0.2, +1, -0.4)`; direção central da superfície para a luz `(-0.182574, -0.912871, +0.365148)`.

A análise CPU localiza o triângulo mais próximo de cada raio com hit GPU usando as posições, índices e câmera capturados. Decodifica as normais UBYTE4 como `xyz / 127 - 1` e orienta cada normal de vértice para a normal do triângulo virada à câmera antes de interpolá-la, seguindo `surface_interaction.slangh:361` e `:425`. `ray_interaction.slangh:50` define `viewDirection=-ray.direction`.

Em todos os três frames, o produto das normais orientadas com a direção central original fica entre **-0.912871 e -0.182574**. Para qualquer direção dentro do cone de meia abertura 2.5°, o maior produto continua negativo: entre **-0.894195 e -0.139514**. Nenhum pixel do modelo CPU recebe essa luz na face visível. Com a direção invertida, o menor produto permitido pelo cone fica entre **+0.139514 e +0.894195**, positivo em todos os hits.

As 12 e 11 interseções de borda dos frames 0 e 1 têm normais brutas voltadas para o outro lado; a orientação de normais feita pelo Remix elimina os produtos positivos que uma análise das normais brutas isoladamente sugeriria. O frame 2 não precisa dessa inversão de borda.

Esse cálculo é uma previsão CPU, não uma leitura das normais finais GPU: exclui `getBentNormal` e diferenças de precisão float/half. O teste A/B GPU confirma a consequência prevista sem depender de assumir que o modelo reproduz cada operação do shader bit a bit.

## Controle e proveniência

Casos preservados:

- Escuro: `runtime/_build/edf-pt-x64-probe/probe-output/session_1791488375451875_25904`.
- Iluminado: `runtime/_build/edf-pt-x64-lit/probe-output/session_1791488778002148_33280`.

As configurações diferem somente em `fallbackLightDirection` e nos dois diretórios de saída dos diagnósticos. O hash SHA-256 de `d3d9.dll` é **93b265b00cb55b51b25c5b7f0495d72a9e0cf76d55abe1462f932501843b4641** em ambos; o do replay é **f4cf480f406573f7d777d9c3b645577e6930ab53af555f4aa8ba5af84296594d** em ambos.

Mesh: `925aa18481228b01392a574dbe28ba4e21aa1616`; geometria RTX: `0x32aeb7bb7e772c13`; material: `0x42a663d7392780d4`; VS: `f098f457d9ae603bea5f84ec985d473c29cfb0c9`; PS: `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`.

`analyze_probe_illumination.json` registra os caminhos completos e SHA-256 dos inputs, configurações, binários, logs, metadados e imagens raw, além dos cálculos por frame. Pode ser regenerado com:

```powershell
& '.tools\build-python\Scripts\python.exe' 'analysis\edf\pathtrace_probe\analyze_probe_illumination.py'
```

O RGB não zero de `final_offscreen` isoladamente não comprova iluminação direta: esse slot contém o resultado após composição interna/pós-processamento e pode incluir sky, iluminação indireta, tonemapping e dither. A prova deste experimento é o slot direto separado, com conclusão GPU e controle A/B. A iluminação usada é artificial e controlada; a iluminação do EDF continua não recuperada.
