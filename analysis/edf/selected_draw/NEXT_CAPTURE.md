# Recaptura restrita ao shader estático validado

Este procedimento mantém o ray tracing desligado e filtra o par de shaders medido. O perfil não contém um decoder de renderização. O objetivo é obter três janelas completas e reencontrar a mesh de 84 vértices/56 triângulos identificada no relatório.

## 1. Trocar o pacote de diagnóstico

Feche o EDF antes de executar. O pacote novo fica separado do anterior. `Restore` recupera o estado anterior à instalação gerenciada; `Install` cria outro backup. Se não existir instalação gerenciada ativa, pule o comando `Restore`. Se ele detectar arquivos alterados, preserve os arquivos e examine a mensagem antes de continuar.

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
$game = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
if (Test-Path -LiteralPath (Join-Path $game '.edf-remix-backup\active.json')) {
  .\tools\edf_capture\deploy.ps1 -Action Restore -GameRoot $game
  if (-not $?) { throw 'A restauração falhou; não instalar outro pacote.' }
}
.\tools\edf_capture\deploy.ps1 -Action Install -GameRoot $game `
  -BundleRoot 'D:\EDF IA RTX REMIX\packages\queue64\edf-diagnostics-x86' `
  -ProfilePath 'D:\EDF IA RTX REMIX\runtime\tools\edf_capture\edf-static-observe.conf'
if (-not $?) { throw 'A instalação falhou; não iniciar o jogo.' }
Start-Process 'steam://rungameid/23530'
```

O perfil usa `rtx.compatCapture.maxQueueMiB=64`, orçamento total de 256 MiB, F10 e um frame por acionamento. A fila inclui o item em escrita. Essa opção exige a DLL nova; a DLL antiga não configura a fila por essa chave.

## 2. Capturar na mesma missão

1. Entre na missão e enquadre cenário opaco. Prefira o local das capturas anteriores. O [wireframe da candidata](<D:/EDF IA RTX REMIX/analysis/edf/selected_draw/geometry_uv_projection.png>) ajuda a reconhecer a forma, mas ainda não prova o nome do objeto.
2. Pare os controles da câmera e pressione F10. Aguarde pelo menos dois segundos.
3. Sem mover os controles, pressione F10 novamente. Aguarde pelo menos dois segundos.
4. Mova a câmera mantendo o mesmo cenário visível e pressione F10 uma terceira vez.
5. Feche o jogo para concluir a escrita. Anote em um arquivo a ordem dos acionamentos e qualquer movimento acidental; não renomeie arquivos dentro das janelas.

As pastas novas aparecem em `D:\EDF IA RTX REMIX\captures\edf-static`. O filtro de shaders admite várias meshes dessa categoria. A identificação da candidata será feita pelos bytes usados, não pelo número da draw. Se ela não estiver presente, a captura não deve ser tratada como teste dessa mesh.

## 3. Verificar cada janela

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
$python = 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe'
$captureRoot = 'D:\EDF IA RTX REMIX\captures\edf-static'
foreach ($capture in Get-ChildItem -LiteralPath $captureRoot -Directory | Sort-Object Name) {
  & $python .\tools\edf_capture\analyze_capture.py (Join-Path $capture.FullName 'records.jsonl') `
    --output (Join-Path $capture.FullName 'inspection')
  if ($LASTEXITCODE) {
    Write-Warning "Inspeção incompleta ou inválida: $($capture.Name)"
  }
}
& $python .\tools\edf_capture\validate_edf_draw.py `
  'D:\EDF IA RTX REMIX\captures\edf' `
  --additional-capture-root $captureRoot `
  --output 'D:\EDF IA RTX REMIX\analysis\edf\next_validation'
```

Examine os registros `capture_status`/`capture_end` em `records.jsonl`: `max_queue_bytes=67108864`, `peak_queue_bytes<=max_queue_bytes`, `truncated=false`, fim `window_complete`. O analisador também informa falhas de integridade. Uma janela vazia pode indicar que o par de shaders não foi usado; uma janela truncada é apenas um prefixo, não prova de ausência da mesh.

`next_validation\validation.json` lista as correspondências positivas da mesh, os frames e os erros da decodificação. O comando conserva como referência a captura antiga -6/draw284. Não atribui intenção de movimento automaticamente. Para o próximo teste, precisamos de uma correspondência positiva nas três janelas e da ordem registrada dos acionamentos.

## 4. Restaurar depois dos testes

Com o jogo fechado:

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
$game = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
.\tools\edf_capture\deploy.ps1 -Action Restore -GameRoot $game
```

## Limite deste experimento

Ele valida repetição e movimento usando os dados de entrada. A sobreposição com raster original, o conteúdo da textura e a criação de uma instance RTX ainda dependem de um experimento posterior restrito à draw comprovada. A fila de 64 MiB foi testada em harness/probe controlado; a recaptura acima verifica o resultado na carga real do EDF.
