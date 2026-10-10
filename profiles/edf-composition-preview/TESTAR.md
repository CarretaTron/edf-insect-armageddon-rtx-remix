# Preview RTX de um frame no EDF

O pacote desta etapa contém o mesmo decoder estrito de 252 vértices / 144 triângulos e um painel limitado à imagem WSI. A composição está **desligada por padrão**. `Enable` permite solicitar um frame com F9; não inicia PT automaticamente. F8 cancela trabalho ainda pendente. A iluminação é de diagnóstico. O painel não demonstra replacement com oclusão correta nem PT contínuo.

O retângulo é `[1040,800,820,200]`, em 1920×1080. Ele pode cobrir elementos do HUD nessa região durante o frame solicitado. Os pixels externos devem permanecer idênticos; backbuffer e três MRTs são comparados integralmente. Depth/stencil não é escrito pelo compositor, mas esta instrumentação não exporta uma comparação binária completa do depth original.

**Pacote experimental preparado para o próximo teste manual.** A versão corrigida passou 18 casos GPU nativos (12 x64 e seis via Bridge x86), 115 testes Python, 15 contratos estáticos e 34 assertions de deployment. A synchronization validation confirmou a remoção do erro de lifetime do descriptor pool encontrado na primeira candidata, mas ainda acusa erros genéricos de renderpass/samplers/layout também presentes no baseline desligado: `globalVulkanValidationPassed=false`. Não há aprovação global Vulkan nem comprovação do painel em gameplay real do EDF. **Nenhum comando abaixo foi executado na instalação ativa pelo agente.**

## Instalar com composição desligada

Feche EDF e Bridge. Abra PowerShell e execute este bloco inteiro:

```powershell
$ErrorActionPreference = 'Stop'
$edfWorkspace = 'D:\EDF IA RTX REMIX'
$edfGame = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
$edfDeploy = Join-Path $edfWorkspace 'runtime\tools\edf_composition\deploy.ps1'
$edfBundle = Join-Path $edfWorkspace 'packages\edf-composition-preview\edf-diagnostics-x86'
$edfProfile = Join-Path $edfWorkspace 'profiles\edf-composition-preview\rtx.conf'
$edfPython = Join-Path $edfWorkspace '.tools\build-python\Scripts\python.exe'
$edfProbeRoot = Join-Path $edfWorkspace 'captures\edf-composition'
$edfExpectedRuntimeSha256 = 'b051fe14b0965c7a45d3a2370d8dc68fc7a18e6ae8ba43ef4a5c4e0d7d745f9e'
if ((Get-FileHash -LiteralPath (Join-Path $edfBundle '.trex\d3d9.dll') -Algorithm SHA256).Hash -ne $edfExpectedRuntimeSha256) {
  throw 'O pacote não contém o Runtime exato desta entrega.'
}

& $edfDeploy -Action Install -GameRoot $edfGame -BundleRoot $edfBundle -ProfilePath $edfProfile
& $edfDeploy -Action Verify -GameRoot $edfGame
```

O instalador exige o fingerprint validado de `EDF-IA.exe`, o manifesto completo de arquivos SHA256, cliente Bridge x86 e Runtime x64. Ele mantém um journal próprio em `.edf-composition-backup`. **Não execute o Restore do instalador antigo `.edf-remix-backup` antes deste Install.** O estado atual, incluindo o pacote anterior e seu `rtx.conf`, é o estado que este novo backup deverá restaurar. O namespace e journal antigos são preservados.

`Verify` deverá informar `Enabled: False`. Se existir `rtx-remix\user.conf`, confira se ele não sobrescreve `rtx.edfStatic.composition`, resolução, upscaler, câmera, debug view ou outros settings do perfil. Preserve o arquivo do usuário; este instalador não o modifica. O log de inicialização identifica configurações aplicadas e os guards do Runtime recusam estados incompatíveis.

Opcionalmente abra o jogo com composição desligada, entre na região validada, pressione F9 e confira que ocorre somente o probe offscreen. Feche-o antes de ativar o painel.

## Ativar manualmente e testar um frame

Com EDF/Bridge fechados, na mesma janela PowerShell:

```powershell
& $edfDeploy -Action Enable -GameRoot $edfGame
& $edfDeploy -Action Verify -GameRoot $edfGame
$edfTestStart = Get-Date
```

`Verify` deverá informar `Enabled: True`. A ativação é lida antes da criação da swapchain, portanto exige reiniciar o jogo; trocar o arquivo durante a execução não autoriza novos recursos WSI. Abra EDF pelo Steam, mantenha **1920×1080**, sem frame generation, upscaler, free camera, screenshot Remix ou debug view. Entre na mesma missão/região e mantenha a candidata visível. Pressione e solte **F9 uma vez**. Um painel com borda ciano deverá mostrar o resultado RTX recortado do frame atual. Ele dura somente esse frame; o frame seguinte volta à apresentação raster. Não é uma imagem de uma captura anterior.

A preparação de shaders e os readbacks podem causar uma pausa. Aguarde a conclusão GPU antes de pressionar novamente. A falta de mesh/câmera atual cancela o request após 300 frames; F8 cancela a espera. F8 não desfaz comandos já submetidos. Se uma comparação GPU falhar, novas tentativas ficam desativadas até reiniciar o processo.

Para acompanhar a execução, em outra janela PowerShell:

```powershell
$edfLog = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon\rtx-remix\logs\remix-dxvk.log'
Get-Content -LiteralPath $edfLog -Tail 60 -Wait |
  Select-String -Pattern '\[EDF-Static-(Probe|Composition)\]|SYNC-HAZARD-|VUID-|VK_ERROR_DEVICE_LOST|shader compilation failed|command submission failed'
```

Se `DXVK_LOG_PATH` redirecionar o log, use esse caminho. Confirme a sequência `preview_generated`, `composition_recorded`, `presentation_queued`, `composition_gpu_completed`, com frame/request correspondentes. O último deve registrar `gpu_completed=1`, `composition_performed=1`, `outside_changed_pixels=0`, `panel_mismatch_pixels=0`, `original_targets_unchanged=1`, `passed=1`. Apenas `composition_recorded` não demonstra execução GPU.

Feche o jogo normalmente depois do teste para finalizar os arquivos. Reserve espaço para o probe original mais as imagens WSI e os novos checks dos quatro targets, aproximadamente 200 MiB por request; o backup do Runtime exige espaço adicional.

## Verificar os dados e exportar a imagem apresentada

Execute na primeira janela PowerShell, preservando as variáveis anteriores:

```powershell
$edfSessions = @(Get-ChildItem -LiteralPath $edfProbeRoot -Directory -Filter 'session_*' |
  Where-Object { $_.CreationTime -ge $edfTestStart })
if ($edfSessions.Count -ne 1) {
  $edfSessions | Select-Object FullName, CreationTime
  throw 'Selecione exatamente a sessão desta execução, pelo output_directory do log.'
}
$edfSession = $edfSessions[0].FullName
$edfLogs = @()
if ($env:DXVK_LOG_PATH) {
  $edfConfigured = Join-Path $env:DXVK_LOG_PATH 'remix-dxvk.log'
  if (Test-Path -LiteralPath $edfConfigured -PathType Leaf) { $edfLogs += Get-Item -LiteralPath $edfConfigured }
}
$edfLogs += Get-ChildItem -LiteralPath $edfGame -Filter 'remix-dxvk.log' -Recurse -Force -File
$edfLiveLog = $edfLogs | Where-Object { $_.LastWriteTime -ge $edfTestStart } |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $edfLiveLog) { throw 'O log desta execução não foi encontrado.' }
$edfAudit = Join-Path $edfWorkspace ('analysis\edf\composition\game-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $edfAudit | Out-Null
$edfSavedLog = Join-Path $edfAudit 'remix-dxvk.log'
Copy-Item -LiteralPath $edfLiveLog.FullName -Destination $edfSavedLog
$edfProbeResult = Join-Path $edfAudit 'probe_verification.json'
$edfCompositionResult = Join-Path $edfAudit 'composition_verification.json'
$edfImageOutput = Join-Path $edfAudit 'images'
$edfRuntime = Join-Path $edfGame '.trex\d3d9.dll'
if ((Get-FileHash -LiteralPath $edfRuntime -Algorithm SHA256).Hash -ne $edfExpectedRuntimeSha256) {
  throw 'O Runtime instalado mudou desde a preparação do pacote.'
}

& $edfDeploy -Action Verify -GameRoot $edfGame
& $edfPython (Join-Path $edfWorkspace 'runtime\tools\edf_static_gpu_replay\verify_probe.py') `
  $edfSession --runtime-log $edfSavedLog --expected-samples 1 --output $edfProbeResult
if ($LASTEXITCODE) { throw 'Falhou a verificação offscreen. Preserve log e sessão.' }
& $edfPython (Join-Path $edfWorkspace 'runtime\tools\edf_static_gpu_replay\verify_composition.py') `
  $edfSession --runtime-log $edfSavedLog --expected-samples 1 `
  --probe-verification $edfProbeResult --runtime $edfRuntime `
  --image-output $edfImageOutput --output $edfCompositionResult
if ($LASTEXITCODE) { throw 'Falhou a composição. Preserve log, sessão e resultado; não expanda o decoder.' }
Get-Content -LiteralPath $edfCompositionResult -Raw
Start-Process explorer.exe -ArgumentList @($edfImageOutput)
```

O verificador recusa eventos de falha do compositor/probe e erros globais de submissão, sincronização Vulkan, validação/SPIR-V ou device-lost, mesmo que uma amostra isolada tenha `passed=true`. Preserve o log completo; o filtro do acompanhamento ao vivo não substitui essa auditoria. Os erros Vulkan genéricos encontrados no baseline dos replays não são ignorados pelo verificador de uma sessão real. Se aparecerem no próximo teste, guarde a sessão e trate o resultado como reprovado; não remova linhas do log para aprová-lo. Rejeições documentadas como câmera/mesh ausentes, gamma não identidade, ROI inválido ou Present parcial deixam o raster e não constituem, isoladamente, erro de corrupção.

O arquivo `edf_pt_request_<request>_frame_<frame>.json` descreve a fase offscreen e continua com `compositionPerformed=false`. O sidecar `edf_pt_request_<request>_frame_<frame>_composition.json` descreve a escrita WSI; a verificação final correlaciona ambos e exige os mesmos frame, request, câmera e hashes. Não altere esses campos para contornar falhas.

O verificador compara o painel GPU com uma reconstrução CPU independente dos buffers final/depth daquele frame. Ele exige igualdade byte a byte fora do ROI, cópia fiel do painel, snapshot original do ROI e preservação dos quatro targets. `wsi_after.png` mostra o resultado GPU enfileirado para apresentação; não é uma captura do desktop nem prova de scanout. Confirme também sua observação dentro do jogo.

Para testar três requests, pressione F9 três vezes na mesma execução, esperando cada conclusão: câmera parada, ainda parada e depois movida com a mesh visível. Use `--expected-samples 3` nos **dois** verificadores. Não misture o baseline desligado de outra execução com a sessão habilitada.

## Desativar e restaurar

Para manter o novo Runtime instalado e voltar à composição desligada, feche jogo/Bridge e execute:

```powershell
& $edfDeploy -Action Disable -GameRoot $edfGame
& $edfDeploy -Action Verify -GameRoot $edfGame
```

Para restaurar exatamente o estado anterior ao experimento, incluindo o Runtime/perfil previamente ativos, feche jogo/Bridge e execute na mesma janela PowerShell. Este comando funciona tanto com o perfil controlado ligado quanto desligado:

```powershell
& $edfDeploy -Action Restore -GameRoot $edfGame
```

O journal novo fica preservado para auditoria; seu `active.json` é retirado após a recuperação completa. O journal legado continua intacto. Não apague backups nem use o Restore antigo enquanto o novo está ativo. Alterações desconhecidas feitas nos arquivos gerenciados após Install fazem o Restore recusar antes de sobrescrevê-las; preserve a mensagem para análise. Enable/Disable usam exclusivamente duas variantes do perfil verificadas por SHA256 e não autorizam sobrescrever edições manuais.
