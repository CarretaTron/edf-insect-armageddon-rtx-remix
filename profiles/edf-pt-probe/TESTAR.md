# Experimento de path tracing offscreen da única mesh validada

Use este pacote **após a entrega final confirmar os testes e a integridade do bundle**. Os comandos abaixo são para você executar depois; o jogo não foi instalado nem iniciado automaticamente. Esta instrução não afirma que o novo experimento já passou dentro do EDF.

O decoder continua restrito à mesh de **252 vértices / 144 triângulos**, por seus hashes, layout e passe opaco comprovados. O experimento prepara um frame RTX em recursos separados e salva resultados após a conclusão GPU. **A imagem original do jogo permanece na tela:** não há substituição visual, blit RTX para o EDF nem suporte a outras meshes. A iluminação de diagnóstico não reproduz a iluminação original do jogo.

| Controle | Comportamento |
|---|---|
| F9 | Solicita um frame offscreen compatível. Solte a tecla depois de pressionar. |
| F8 | Cancela frames ainda pendentes. Comandos já enviados à GPU e seus readbacks podem terminar depois. |
| F10 | Captura opcional da instrumentação anterior; não é necessário para validar o probe. |

O perfil fornecido usa `probe.enabled=True`, `frameCount=1` e `autorun=False`. `rasterOnly=True` permanece ativo. Alterar o valor legado `rasterOnly=False` não habilita substituição da imagem do jogo.

## 1. Verificar o pacote e instalar com backup

Feche o EDF e `NvRemixBridge.exe`. Abra PowerShell e execute o bloco inteiro. O jogo é x86: o bundle correto contém o cliente `d3d9.dll` de 32 bits na raiz e o servidor/Runtime x64 em `.trex`.

O bloco verifica o manifesto, o Runtime exato e o fingerprint do jogo **antes** de restaurar o pacote gerenciado anterior e instalar o novo. O script de deploy cria um journal com backup dos arquivos substituídos.

```powershell
$ErrorActionPreference = 'Stop'
$edfWorkspace = 'D:\EDF IA RTX REMIX'
$edfGame = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
$edfDeploy = Join-Path $edfWorkspace 'runtime\tools\edf_capture\deploy.ps1'
$edfBundle = Join-Path $edfWorkspace 'packages\edf-pt-probe\edf-diagnostics-x86'
$edfProfile = Join-Path $edfWorkspace 'profiles\edf-pt-probe\rtx.conf'
$edfPython = Join-Path $edfWorkspace '.tools\build-python\Scripts\python.exe'
$edfVerify = Join-Path $edfWorkspace 'runtime\tools\edf_static_gpu_replay\verify_probe.py'
$edfProbeRoot = Join-Path $edfWorkspace 'captures\edf-pt-probe'
$edfRuntimeSha256 = '93b265b00cb55b51b25c5b7f0495d72a9e0cf76d55abe1462f932501843b4641'

if (Get-Process -Name 'EDF-IA', 'NvRemixBridge' -ErrorAction SilentlyContinue) {
  throw 'Feche o jogo e o Bridge antes de instalar.'
}
if ((Get-FileHash -LiteralPath (Join-Path $edfGame 'EDF-IA.exe') -Algorithm SHA1).Hash -ne
    'd2e90641c560420d279a629ddd86e13cb767f464') {
  throw 'O executável difere do fingerprint validado. Não instale este perfil nesta versão.'
}
$edfManifestPath = Join-Path $edfBundle 'package-manifest.json'
if (-not (Test-Path -LiteralPath $edfManifestPath -PathType Leaf)) {
  throw 'O pacote ainda não foi concluído. Aguarde a entrega final.'
}
$edfManifest = Get-Content -LiteralPath $edfManifestPath -Raw | ConvertFrom-Json
if ($edfManifest.architecture -ne 'x86-bridge-client + x64-server/runtime') {
  throw 'Este teste exige o cliente Bridge x86 com servidor/Runtime x64.'
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
if ((Get-FileHash -LiteralPath (Join-Path $edfBundle '.trex\d3d9.dll') -Algorithm SHA256).Hash -ne
    $edfRuntimeSha256) {
  throw 'O renderer do pacote não é o Runtime desta entrega.'
}
& $edfPython -c 'import numpy; print("NumPy:", numpy.__version__)'
if ($LASTEXITCODE) { throw 'NumPy não está disponível no Python do workspace.' }

$edfActive = Join-Path $edfGame '.edf-remix-backup\active.json'
if (Test-Path -LiteralPath $edfActive -PathType Leaf) {
  & $edfDeploy -Action Restore -GameRoot $edfGame
}
& $edfDeploy -Action Install -GameRoot $edfGame -BundleRoot $edfBundle -ProfilePath $edfProfile
$edfTestStart = Get-Date
```

Se o Restore recusar um arquivo alterado, interrompa o procedimento e preserve a mensagem. O journal identifica o arquivo; não apague `active.json` nem modifique os hashes para contornar a verificação.

## 2. Coletar três amostras na mesma região

Abra o EDF pelo Steam e entre na mesma missão/região das capturas estáticas anteriores, com a candidata visível. Mantenha a resolução em **1920×1080** e o perfil de diagnóstico fornecido. Não habilite free camera, jitter, upscaler ou frame generation pelo menu Remix. As opções derivadas de presets também são fixadas pelo perfil para manter a câmera do renderer igual à câmera validada.

Para acompanhar a conclusão GPU, abra outra janela PowerShell e execute o bloco abaixo. O caminho é o usado no teste real anterior; se `DXVK_LOG_PATH` estiver configurado, use o caminho correspondente. `Ctrl+C` encerra apenas o acompanhamento do log.

```powershell
$edfLiveLog = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon\rtx-remix\logs\remix-dxvk.log'
Get-Content -LiteralPath $edfLiveLog -Tail 40 -Wait |
  Select-String -SimpleMatch '[EDF-Static-Probe]'
```

1. Com a câmera parada, pressione e solte **F9**. Aguarde `stage=gpu_completed` com o mesmo `request_id` mostrado por `stage=armed`.
2. Sem mover a câmera, pressione e solte **F9** novamente. Aguarde a segunda conclusão GPU.
3. Mova a câmera moderadamente, mantendo a mesma mesh visível e à frente do near plane. Pressione e solte **F9** e aguarde a terceira conclusão GPU.
4. Feche o jogo normalmente para concluir os logs e readbacks. As três amostras devem pertencer à **mesma execução** do Runtime.

O primeiro F9 pode pausar o jogo durante a compilação síncrona dos pipelines RTX; o replay frio levou aproximadamente 20 segundos nessa preparação, e o tempo no EDF pode variar. Aguarde a conclusão antes de repetir a tecla. Cada request é limitado a **um frame**, sem path tracing contínuo, e grava cerca de **110 MiB** de evidência em 1920×1080; reserve aproximadamente 330 MiB para as três amostras.

O Runtime cria uma pasta `session_<timestamp>_<pid>` no primeiro F9. Os três requests dessa execução ficam nessa mesma pasta, em `D:\EDF IA RTX REMIX\captures\edf-pt-probe`. O caminho exato aparece em `stage=armed ... output_directory=...` e em `stage=gpu_completed ... output=...`.

Se apertar F9 antes de o request anterior terminar, `arm_rejected reason=previous_request_pending` é esperado: aguarde a conclusão e pressione novamente. Se a candidata não estiver sendo aceita, o request pode aguardar um frame compatível e ser cancelado após 300 frames. F8 cancela essa espera. Não use uma região diferente para forçar o teste; preserve os logs de rejeição.

## 3. Conferir a sequência e verificar a sessão exata

Cada request concluído deve mostrar estes eventos:

| Evento | O que comprova |
|---|---|
| `armed` | Um request limitado foi solicitado; não confirma execução GPU. |
| `injection_snapshot` | Origem da injeção, draw count daquele frame, frame da câmera e comparação das matrizes usadas pelo renderer. |
| `whitelist_accepted` | Uma única instance permitida e visível, com a geometria 252/432, na cena usada pelo probe. |
| `offscreen_begin` | Câmera atual aceita para o experimento, com `composition_performed=0`. |
| `readback_recorded` | Cópias de diagnóstico e sinalização de conclusão enfileiradas. |
| `gpu_completed` | Readbacks concluídos após fence; confirme `original_targets_unchanged=1` e `composition_performed=0`. |

`injection_rejected reason=already_injected_this_frame` pode representar a proteção normal contra uma tentativa duplicada. `main_camera_invalid` em um frame sem draw permitida não autoriza usar a câmera anterior. O snapshot distingue esses casos de uma falha com a candidata presente. Guarde o log inteiro.

Depois de fechar o jogo, execute na primeira janela PowerShell, mantendo as variáveis do bloco de instalação. O script seleciona o log e a única sessão de probe criada depois da instalação; recusa múltiplas sessões para evitar misturar execuções. A busca do log usa `DXVK_LOG_PATH`, quando definido, e o diretório do jogo como fallback.

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
if (-not $edfLog) { throw 'Não foi encontrado o log desta execução.' }
$edfSessions = @(Get-ChildItem -LiteralPath $edfProbeRoot -Directory -Filter 'session_*' |
  Where-Object { $_.CreationTime -ge $edfTestStart })
if ($edfSessions.Count -ne 1) {
  $edfSessions | Select-Object FullName, CreationTime
  throw 'Selecione a pasta exata do output_directory desta execução; não misture sessões.'
}
$edfProbeSession = $edfSessions[0].FullName
$edfResult = Join-Path $edfProbeSession 'probe_verification.json'
& $edfPython $edfVerify $edfProbeSession --runtime-log $edfLog.FullName `
  --expected-samples 3 --output $edfResult
if ($LASTEXITCODE) {
  throw "A verificação falhou. Preserve a sessão, o log e $edfResult para análise."
}
$edfResult
```

Não use `--expected-missing-frame` neste teste real: essa opção pertence ao replay sintético de ausência de câmera. Se precisar selecionar a sessão manualmente, atribua seu caminho absoluto a `$edfProbeSession` e execute novamente apenas as linhas do verificador.

O verificador precisa de NumPy e compara o readback de profundidade com interseções CPU independentes da mesh validada, usando a câmera efetiva do renderer. Também confere integridade dos arquivos, layouts, hashes, matrizes, conclusão GPU e igualdade integral dos três MRTs e do alvo final antes/depois do probe. Ele não confirma iluminação original, replacement ou um ponto de composição visual parcial. Uma cor escura ou radiância zero é relatada separadamente e não prova falha geométrica.

**`passed=true` sozinho não comprova iluminação.** O verificador registra `radianceNonzeroRequired=false`: confira `samples[].radianceNonzeroObserved` e `radianceNonzeroCount`, além de `radiance_nonzero` no log. Se a radiância for zero no teste real, preserve os dados; uma validação geométrica bem-sucedida comprova câmera, geometria, readbacks GPU e preservação do raster, mas ainda falta demonstrar iluminação positiva nessa execução. Mesmo radiância positiva vem da luz de diagnóstico, não da iluminação original do EDF.

Uma câmera muito próxima, com a mesh atravessando o near plane, ou uma região onde a candidata não aparece pode rejeitar esta validação controlada. Não altere os guards para fazer o relatório passar; retorne à região original e preserve a sessão que falhou.

Para arquivar também o log antes de iniciar outra execução:

```powershell
Copy-Item -LiteralPath $edfLog.FullName -Destination (Join-Path $edfProbeSession 'remix-dxvk.log')
```

## 4. Cancelar, desativar ou restaurar

F8 cancela requests pendentes. Ele não desfaz comandos já submetidos à GPU. O raster deve permanecer original em todos os modos deste pacote.

Para continuar somente com a reconstrução e os diagnósticos, feche jogo/Bridge e instale o perfil passivo pelo mesmo journal. Evite editar o `rtx.conf` instalado diretamente: alterações podem fazer o Restore recusar um arquivo que difere do manifesto registrado.

```powershell
if (Get-Process -Name 'EDF-IA', 'NvRemixBridge' -ErrorAction SilentlyContinue) {
  throw 'Feche o jogo e o Bridge antes de trocar o perfil.'
}
& $edfDeploy -Action Restore -GameRoot $edfGame
& $edfDeploy -Action Install -GameRoot $edfGame -BundleRoot $edfBundle `
  -ProfilePath (Join-Path $edfWorkspace 'profiles\edf-pt-probe\diagnostics-only.conf')
```

Esse perfil usa o mesmo Runtime, mas `rtx.edfStatic.probe.enabled=False`; F9 não solicita path tracing. Para remover o pacote gerenciado e restaurar seu estado anterior à instalação, com jogo/Bridge fechados:

```powershell
& $edfDeploy -Action Restore -GameRoot $edfGame
```

Para voltar especificamente ao protótipo anterior que você já testou, depois do Restore instale `packages\edf-static-prototype\edf-diagnostics-x86` com `profiles\edf-static-prototype\rtx.conf`, usando o mesmo script de deploy. Os backups/journals e os arquivos de evidência permanecem disponíveis.
