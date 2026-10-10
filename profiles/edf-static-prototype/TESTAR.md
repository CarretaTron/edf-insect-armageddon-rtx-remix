# Testar a candidata estática no EDF

Execute os comandos **depois que a entrega final confirmar a compilação e o pacote concluídos**, com o jogo e `NvRemixBridge.exe` fechados. Este documento não confirma que o teste dentro do EDF já passou.

O perfil aceita somente a geometria validada de **252 vértices utilizados / 144 triângulos**, pelos hashes dos cinco streams, índices, declaração e shaders. O `NumVertices=882` da chamada original inclui um intervalo maior; não é o número de vértices reconstruídos. Outra draw com a mesma contagem não é aceita automaticamente. POSITION, NORMAL e UV são decodificados; a transformação e câmera passam por validação de reprojeção.

`rtx.edfStatic.rasterOnly=True` preserva o raster original e prepara a cena RTX e BLAS/TLAS. **O protótipo não dispara path tracing**, portanto a aparência original deve permanecer. Mantenha `rtx.enableRaytracing=True`: essa opção permite executar o caminho de reconstrução; `rasterOnly` impede a substituição visual nesta etapa.

## 1. Restaurar o pacote gerenciado anterior e instalar o novo

Abra PowerShell e execute o bloco. Ele verifica o manifesto antes de restaurar o pacote `queue64` anterior, caso exista `.edf-remix-backup\active.json`, e instala o novo bundle com o perfil correto. O script mantém o backup dos arquivos substituídos.

```powershell
$ErrorActionPreference = 'Stop'
$edfWorkspace = 'D:\EDF IA RTX REMIX'
$edfGame = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
$edfDeploy = Join-Path $edfWorkspace 'runtime\tools\edf_capture\deploy.ps1'
$edfBundle = Join-Path $edfWorkspace 'packages\edf-static-prototype\edf-diagnostics-x86'
$edfProfile = Join-Path $edfWorkspace 'profiles\edf-static-prototype\rtx.conf'
$edfManifestPath = Join-Path $edfBundle 'package-manifest.json'

if (-not (Test-Path -LiteralPath $edfManifestPath -PathType Leaf)) {
  throw 'O novo pacote ainda não foi concluído. Aguarde a entrega final.'
}
$edfManifest = Get-Content -LiteralPath $edfManifestPath -Raw | ConvertFrom-Json
if ($edfManifest.architecture -ne 'x86-bridge-client + x64-server/runtime') {
  throw 'Este teste exige o bundle x86 do Bridge com Runtime x64.'
}
foreach ($edfFile in $edfManifest.files) {
  $edfSource = Join-Path $edfBundle ([string]$edfFile.path)
  if (-not (Test-Path -LiteralPath $edfSource -PathType Leaf)) {
    throw "Arquivo ausente no pacote: $($edfFile.path)"
  }
  if ((Get-Item -LiteralPath $edfSource).Length -ne $edfFile.size -or
      (Get-FileHash -LiteralPath $edfSource -Algorithm SHA256).Hash -ne $edfFile.sha256) {
    throw "Arquivo diferente do manifesto: $($edfFile.path)"
  }
}

$edfActive = Join-Path $edfGame '.edf-remix-backup\active.json'
if (Test-Path -LiteralPath $edfActive -PathType Leaf) {
  & $edfDeploy -Action Restore -GameRoot $edfGame
}
& $edfDeploy -Action Install -GameRoot $edfGame -BundleRoot $edfBundle -ProfilePath $edfProfile
$edfTestStart = Get-Date
```

Se `Restore` recusar um arquivo alterado, preserve a mensagem e interrompa a instalação: o journal explica qual arquivo difere do backup. Não apague `active.json` para contornar essa verificação.

## 2. Exercitar a mesh

1. Abra o EDF pelo Steam e entre na mesma missão/região das três recapturas `edf-static`, mantendo o cenário observado visível. Entrar no menu não exercita a candidata.
2. Espere alguns segundos com a câmera parada; mova e gire a câmera mantendo o cenário visível. Observe se o raster continua correto e sem deformações.
3. Opcionalmente, pressione F10 com a câmera parada e novamente após movê-la. As janelas de um frame ficam em `D:\EDF IA RTX REMIX\captures\edf-prototype`.
4. Feche o jogo normalmente para concluir logs e capturas. Não é necessário habilitar replacement ou path tracing.

## 3. Conferir a sequência RTX

O nome do log do Runtime é `remix-dxvk.log`. O caminho habitual é `<pasta do jogo>\rtx-remix\logs`; `DXVK_LOG_PATH` pode substituí-lo e o diretório de trabalho do processo pode afetar o caminho padrão. O bloco abaixo consulta o caminho configurado e faz busca recursiva no jogo como fallback. Execute na mesma sessão PowerShell após fechar o EDF.

```powershell
$edfLogCandidates = @()
if ($env:DXVK_LOG_PATH) {
  $edfConfiguredLog = Join-Path $env:DXVK_LOG_PATH 'remix-dxvk.log'
  if (Test-Path -LiteralPath $edfConfiguredLog -PathType Leaf) {
    $edfLogCandidates += Get-Item -LiteralPath $edfConfiguredLog
  }
}
$edfLogCandidates += Get-ChildItem -LiteralPath $edfGame -Filter 'remix-dxvk.log' -File -Recurse -Force
$edfLog = $edfLogCandidates |
  Where-Object { $_.LastWriteTime -ge $edfTestStart } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $edfLog) {
  throw 'Não foi encontrado remix-dxvk.log desta execução. Verifique DXVK_LOG_PATH e o diretório de trabalho do jogo.'
}
$edfLog.FullName
Select-String -LiteralPath $edfLog.FullName -SimpleMatch '[EDF-Static]', '[RTX-Compatibility-Trace]'
```

Procure `profile_enabled` com `vertices=252 triangles=144 preserve_raster=true`. Para `trace=0x4544465354415449`, a sequência esperada ao longo dos primeiros frames é:

| Log | Confirmação |
| --- | --- |
| `stage=recognized` | Hashes e regras da candidata reconhecidos; draw raster preservada. |
| `stage=raster_geometry` | `vertices=252 indices=432`, POSITION/NORMAL FLOAT3 e UV FLOAT2; índices 0…251. |
| `stage=camera_accepted` | CameraManager aceitou a câmera reconstruída. |
| `stage=scene_geometry` | SceneManager recebeu e converteu a geometria. |
| `stage=instance_accepted` | InstanceManager criou/atualizou a instance RTX. |
| `stage=blas_recorded` | O Runtime registrou o build da BLAS; `blas_reused` pode aparecer posteriormente. |
| `stage=tlas_recorded ... included=1` | A instance entrou na TLAS; confirme o tipo opaco e sua associação à BLAS. |
| `stage=scene_only_frame ... raster_preserved=1 path_tracing_dispatched=0` | Preparação da cena concluída com raster original e sem despacho de path tracing. |

Esses logs são deduplicados e têm limite de frequência; não precisam reaparecer a cada frame. Os estágios de build confirmam comandos registrados pelo Runtime, não constituem por si só uma comparação visual do resultado GPU. Nenhum marcador de reconhecimento significa que a mesh pode não estar visível ou uma regra do perfil falhou. `profile_rejected`, `camera_rejected`, `scene_rejected`, `blas_rejected`, `tlas_rejected` ou `injection_rejected` fornecem o motivo do bloqueio; preserve o log completo para a próxima análise.

Para guardar o log sem alterar a instalação:

```powershell
$edfEvidence = Join-Path $edfWorkspace ('analysis\edf\game-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $edfEvidence -Force | Out-Null
Copy-Item -LiteralPath $edfLog.FullName -Destination (Join-Path $edfEvidence 'remix-dxvk.log')
```

## 4. Desfazer o teste

Com o jogo e Bridge fechados, use o mesmo journal:

```powershell
& $edfDeploy -Action Restore -GameRoot $edfGame
```

Isso restaura o estado anterior à instalação deste pacote. Para voltar especificamente ao `queue64`, após o Restore instale o bundle `D:\EDF IA RTX REMIX\packages\queue64\edf-diagnostics-x86` com o perfil de recaptura utilizado anteriormente.
