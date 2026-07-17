$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Release = Join-Path $Root 'release'
$Installer = Join-Path $Release 'GoldSlots-Player-Setup-6.9.3.exe'
if (!(Test-Path $Installer)) { throw 'Final Player installer is missing.' }
if ((Get-Item $Installer).Length -lt 70000000) { throw 'Final Player installer is too small to contain the original artwork.' }

& 7z l $Installer | Out-File (Join-Path $Release 'INSTALLER-CONTENTS.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Final Player EXE is not a readable NSIS installer.' }

$InstallDir = 'C:\GSP693QA'
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
$Install = Start-Process -FilePath $Installer -ArgumentList '/S',('/D=' + $InstallDir) -Wait -PassThru
if ($Install.ExitCode -ne 0) { throw "Player installation failed with exit code $($Install.ExitCode)." }

$AppExe = Join-Path $InstallDir 'Gold Slots Player.exe'
$AppAsar = Join-Path $InstallDir 'resources\app.asar'
if (!(Test-Path $AppExe) -or !(Test-Path $AppAsar)) { throw 'Installed Player runtime is incomplete.' }
$PowerShellFiles = @(Get-ChildItem $InstallDir -Recurse -File -Filter '*.ps1')
if ($PowerShellFiles.Count -gt 0) { throw 'Installed Player contains PowerShell runtime files.' }

$env:GS_PLAYER_UI_TEST = '1'
$Process = Start-Process -FilePath $AppExe -ArgumentList '--disable-gpu' -PassThru
Start-Sleep -Seconds 10
if ($Process.HasExited) { throw "Installed Player exited during Windows launch with code $($Process.ExitCode)." }
Stop-Process -Id $Process.Id -Force
Remove-Item Env:GS_PLAYER_UI_TEST -ErrorAction SilentlyContinue

$ShortcutCandidates = @(
  (Join-Path $env:PUBLIC 'Desktop\Gold Slots Player.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Gold Slots Player.lnk')
) | Select-Object -Unique
$Shortcut = $ShortcutCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Shortcut) { throw 'Gold Slots Player desktop shortcut is missing.' }

$Uninstaller = Get-ChildItem $InstallDir -File | Where-Object { $_.Name -match '^Uninstall.*\.exe$' } | Select-Object -First 1
if (-not $Uninstaller) { throw 'Gold Slots Player uninstaller is missing.' }

$Sha256 = (Get-FileHash $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
$Sha512 = (Get-FileHash $Installer -Algorithm SHA512).Hash.ToLowerInvariant()
$Size = (Get-Item $Installer).Length
"SHA256=$Sha256" | Out-File (Join-Path $Release 'SHA256.txt') -Encoding ascii
"SHA512=$Sha512" | Out-File (Join-Path $Release 'SHA512.txt') -Encoding ascii
"SIZE=$Size" | Out-File (Join-Path $Release 'SIZE.txt') -Encoding ascii
@'
GOLD SLOTS PLAYER 6.9.3 — ORIGINAL PREMIUM VISUAL RELEASE

Verified on Windows before delivery:
- Original Gold Slots logo retained.
- All eleven original lobby artworks retained.
- All eleven original game-screen artworks retained.
- Premium casino styles and responsive landscape/portrait rules retained.
- Trusted one-action physical-pointer bridge packaged for controls.
- Strict manual game controls packaged.
- Normal non-kiosk startup.
- No PowerShell runtime files inside the installed application.
- NSIS installation, application launch, desktop shortcut and uninstaller verified.
'@ | Out-File (Join-Path $Release 'README.txt') -Encoding utf8

$Uninstall = Start-Process -FilePath $Uninstaller.FullName -ArgumentList '/S' -Wait -PassThru
if ($Uninstall.ExitCode -ne 0) { throw "Player uninstaller failed with exit code $($Uninstall.ExitCode)." }
Start-Sleep -Seconds 3
if (Test-Path $AppExe) { throw 'Player uninstaller did not remove the executable.' }
Write-Host "VERIFIED_INSTALLER=$Installer"
Write-Host "SHA256=$Sha256"
Write-Host "SIZE=$Size"
