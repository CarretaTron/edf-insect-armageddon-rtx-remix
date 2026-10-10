# Próximo teste disponível: visualizar os readbacks reais

Esta etapa não precisa iniciar o EDF, instalar DLL ou editar `rtx.conf`. O renderer ativo continua sendo o experimento offscreen já testado. As opções de composição descritas no plano ainda não foram implementadas.

## Abrir os resultados já gerados

No PowerShell:

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
Invoke-Item '.\analysis\edf\visualization\images\comparison_final_offscreen.png'
Invoke-Item '.\analysis\edf\visualization\images\comparison_direct_diffuse_radiance.png'
Invoke-Item '.\analysis\edf\visualization\images\comparison_albedo.png'
Invoke-Item '.\analysis\edf\visualization\images\comparison_linear_z.png'
Invoke-Item '.\analysis\edf\visualization\images\comparison_original_backbuffer.png'
```

As comparações têm a mesma região de tela e escala. A saída final conserva RGB já processado pelo Runtime. Radiância usa exposição EV=0 e Reinhard; albedo usa somente transferência sRGB. A profundidade é distância positiva em espaço de câmera, com azul próximo/vermelho distante. Fundo preto e ausência de partes do cenário são esperados: somente a mesh de 252 vértices foi incluída no RTX.

## Executar novamente o conversor

O ambiente Python do workspace já contém NumPy e Pillow. Este comando escolhe um diretório novo em cada execução, preservando capturas e resultados anteriores:

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
$viewerOutput = Join-Path (Get-Location).Path ('analysis\edf\visualization\rerun_' + [Guid]::NewGuid().ToString('N'))
& '.\.tools\build-python\Scripts\python.exe' `
  '.\runtime\tools\edf_probe_viewer\view_capture.py' `
  '.\captures\edf-pt-probe\session_1791490947848472_12776' `
  --output $viewerOutput `
  --verification '.\captures\edf-pt-probe\session_1791490947848472_12776\probe_verification.json' `
  --final-encoding display `
  --exposure-ev 0 `
  --expected-samples 3
if ($LASTEXITCODE -ne 0) { throw 'O visualizador rejeitou a captura; confira a mensagem acima.' }
Invoke-Item (Join-Path $viewerOutput 'comparison_final_offscreen.png')
Invoke-Item (Join-Path $viewerOutput 'comparison_direct_diffuse_radiance.png')
```

Resultado esperado: `passed=true`, `sampleCount=3`, `originalFilesPreserved=true`, `pngCount=42`, `finalEncoding=display`. `viewer_manifest.json` registra hashes, formatos, clamping e transformação aplicada. Se houver arquivo ausente, checksum incorreto ou captura incompleta, o processo termina com erro; não use os PNGs parciais como evidência.

Para comparar outra exposição da **radiância intermediária**, repita com um novo diretório e `--exposure-ev -1` ou `1`. Essa opção não muda albedo ou saída final no modo `display`. Não escolha `hdr` para a saída final apenas porque o arquivo é float16: isso reaplicaria uma curva sobre valores que já passaram pelo processamento final no caminho normal auditado. Os flags de transferência/debug por frame não foram armazenados nesta sessão.

## Avaliar a proposta de painel

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
Invoke-Item '.\analysis\edf\visualization\mockup\request_1_frame_1043_offline_preview_mockup.png'
Invoke-Item '.\analysis\edf\visualization\mockup\request_2_frame_1112_offline_preview_mockup.png'
Invoke-Item '.\analysis\edf\visualization\mockup\request_3_frame_1171_offline_preview_mockup.png'
```

Essas imagens são simulações offline do ROI `[1040,800,820,200]`, adequado apenas às capturas 1920×1080 atuais. Não houve composição GPU no jogo. A posição foi escolhida sobre o asfalto inferior direito para não cobrir o HUD observado; outras situações podem precisar de outra posição.

O próximo incremento proposto implementará esse preview na imagem WSI, desligado por padrão. Antes de qualquer instalação, deverá passar por replay x64/x86: frame válido, fence, original raster/MRTs inteiramente iguais e apresentação igual fora do ROI; rejeição de câmera antiga, mesh ausente, hashes diferentes, duplicação, ROI inválido e Reset. O semaphore de apresentação deverá ser sinalizado somente depois de concluir o overlay.

Somente depois desses testes poderá ser entregue um pacote de composição separado. O protocolo futuro será F9 por um frame compatível e F8 para cancelar pedidos pendentes; retornar ao raster nos frames seguintes é requisito. **F9 no pacote instalado atualmente continua produzindo apenas a captura offscreen.** Não há comando disponível nesta entrega para habilitar composição dentro do jogo.
