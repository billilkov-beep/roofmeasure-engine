$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path 'release' | Out-Null
$transcript = Join-Path (Resolve-Path 'release') 'INSTALL-TEST-TRANSCRIPT.txt'
Start-Transcript -Path $transcript -Force | Out-Null

function Write-Stage([string]$message) {
  $line = "$(Get-Date -Format o) :: $message"
  Write-Host $line
  Add-Content -Path 'release/INSTALL-TEST-STAGES.txt' -Value $line
}

try {
  Write-Stage 'Resolving generated installer'
  $installer = Resolve-Path 'release/GoldSlots-Player-Setup-6.9.2.exe'
  $installerSize = (Get-Item $installer).Length
  Write-Stage "Installer size: $installerSize bytes"
  if ($installerSize -lt 40000000) { throw 'Installer payload is incomplete.' }

  Write-Stage 'Inspecting installer with 7-Zip'
  & 7z l $installer 2>&1 | Tee-Object -FilePath 'release/INSTALLER-CONTENTS.txt' | Out-Host
  if ($LASTEXITCODE -ne 0) { throw 'Generated EXE is not a readable installer.' }

  $installDir = 'C:\GSP692QA'
  Write-Stage "Cleaning test directory $installDir"
  if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force }

  Write-Stage 'Running silent assisted NSIS installation'
  $install = Start-Process -FilePath $installer -ArgumentList '/S',('/D=' + $installDir) -Wait -PassThru
  Write-Stage "Installer exit code: $($install.ExitCode)"
  if ($install.ExitCode -ne 0) { throw "Installer failed with exit code $($install.ExitCode)." }

  Write-Stage 'Listing installed files'
  Get-ChildItem $installDir -Recurse -File | Select-Object FullName,Length | Format-Table -AutoSize | Out-String -Width 300 | Out-File 'release/INSTALLED-FILES.txt' -Encoding utf8

  $appExe = Join-Path $installDir 'Gold Slots Player.exe'
  $asar = Join-Path $installDir 'resources\app.asar'
  if (-not (Test-Path $appExe)) { throw "Installed Player executable is missing at $appExe." }
  if (-not (Test-Path $asar)) { throw "Installed Electron app.asar is missing at $asar." }
  if ((Get-ChildItem $installDir -Recurse -File -Filter '*.ps1').Count -gt 0) { throw 'Installed package contains a PowerShell script.' }

  Write-Stage 'Launching and click-testing the installed Player'
  $env:GS_PLAYER_EXE = $appExe
  npm run test:ui 2>&1 | Tee-Object -FilePath 'release/INSTALLED-E2E.txt' | Out-Host
  $testExit = $LASTEXITCODE
  Remove-Item Env:GS_PLAYER_EXE -ErrorAction SilentlyContinue
  Write-Stage "Installed UI test exit code: $testExit"
  if ($testExit -ne 0) { throw "Installed Player UI test failed with exit code $testExit." }

  Write-Stage 'Checking Desktop shortcut'
  $shortcutCandidates = @(
    (Join-Path $env:PUBLIC 'Desktop\Gold Slots Player.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Gold Slots Player.lnk')
  ) | Select-Object -Unique
  $shortcutCandidates | Out-File 'release/SHORTCUT-CANDIDATES.txt' -Encoding utf8
  $desktopShortcut = $shortcutCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $desktopShortcut) { throw 'Desktop shortcut was not created in the public or current-user Desktop folder.' }
  Write-Stage "Desktop shortcut found: $desktopShortcut"

  Write-Stage 'Checking and running Windows uninstaller'
  $uninstaller = Get-ChildItem $installDir -File | Where-Object { $_.Name -match '^Uninstall.*\.exe$' } | Select-Object -First 1
  if (-not $uninstaller) { throw 'Windows uninstaller is missing.' }
  $uninstall = Start-Process -FilePath $uninstaller.FullName -ArgumentList '/S' -Wait -PassThru
  Write-Stage "Uninstaller exit code: $($uninstall.ExitCode)"
  if ($uninstall.ExitCode -ne 0) { throw "Uninstaller failed with exit code $($uninstall.ExitCode)." }
  Start-Sleep -Seconds 3
  if (Test-Path $appExe) { throw 'Uninstaller did not remove the installed executable.' }

  Write-Stage 'Generating release checksums and verification report'
  $sha256 = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
  $sha512 = (Get-FileHash $installer -Algorithm SHA512).Hash.ToLowerInvariant()
  "SHA256=$sha256" | Out-File 'release/SHA256.txt' -Encoding ascii
  "SHA512=$sha512" | Out-File 'release/SHA512.txt' -Encoding ascii
  "SIZE=$installerSize" | Out-File 'release/SIZE.txt' -Encoding ascii
  @'
GOLD SLOTS PLAYER 6.9.2 — WINDOWS-TESTED PRODUCTION CANDIDATE

Automated Windows validation completed before publication:
- Clean assisted NSIS installation.
- Installed app.exe and resources/app.asar verified.
- Source and installed Player launched through Electron.
- Native mouse clicks tested; no synthetic click rescue layer.
- All 11 public game cards visible and non-overlapping.
- Layout tested at 1024x576, 1366x768, 1920x1080 and 800x1000 portrait.
- Hi-Lo requires manual stake and Higher/Lower selection.
- Roulette requires manual stake and table selection.
- Blackjack requires Deal, then manual Hit or Stand.
- Jacks or Better requires Deal, manual Hold, then Draw.
- Stake and decisions reset after each completed round.
- Sound, back/lobby, Finish and next-player controls clicked.
- Normal maximized startup; kiosk activates only after fresh Super Admin policy.
- No PowerShell runtime or .ps1 files installed.
- Desktop shortcut and Windows uninstaller verified.
'@ | Out-File 'release/README.txt' -Encoding utf8
  Write-Stage 'ALL INSTALL AND UI TESTS PASSED'
}
catch {
  $_ | Format-List * -Force | Out-String -Width 300 | Out-File 'release/INSTALL-TEST-ERROR.txt' -Encoding utf8
  Write-Stage "FAILURE: $($_.Exception.Message)"
  throw
}
finally {
  Stop-Transcript | Out-Null
}
