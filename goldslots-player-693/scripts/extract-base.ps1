$ErrorActionPreference='Stop'
$baseInstaller=Resolve-Path 'work/GoldSlots-Player-Setup-6.8.0-Original-Visual.exe'
$nsisDir=Join-Path (Resolve-Path 'work') 'nsis-extracted'
$baseDir='C:\GSP680BASE'
if(Test-Path $nsisDir){Remove-Item $nsisDir -Recurse -Force}
if(Test-Path $baseDir){Remove-Item $baseDir -Recurse -Force}
New-Item -ItemType Directory -Force -Path $nsisDir,$baseDir|Out-Null

Write-Host 'Inspecting original visual NSIS container.'
& 7z l -slt $baseInstaller | Out-File 'work/BASE-INSTALLER-CONTENTS.txt' -Encoding utf8
if($LASTEXITCODE -ne 0){throw 'The verified original visual EXE cannot be read by 7-Zip.'}

Write-Host 'Extracting NSIS container without installing or launching the old build.'
& 7z x $baseInstaller "-o$nsisDir" -y | Out-Host
if($LASTEXITCODE -ne 0){throw 'Could not extract the original visual NSIS container.'}

$payload=Get-ChildItem $nsisDir -Recurse -File|Where-Object{$_.Name -match '^app-(?:64|x64)\.7z$|^app\.7z$'}|Select-Object -First 1
if(-not $payload){
  $payload=Get-ChildItem $nsisDir -Recurse -File|Where-Object{$_.Extension -eq '.7z' -and $_.Length -gt 40000000}|Sort-Object Length -Descending|Select-Object -First 1
}
if(-not $payload){
  Get-ChildItem $nsisDir -Recurse -File|Select-Object FullName,Length|Format-Table -AutoSize|Out-String -Width 300|Out-File 'work/BASE-EXTRACTED-FILES.txt' -Encoding utf8
  throw 'The original visual application payload was not found inside the NSIS container.'
}
Write-Host "Extracting original runtime payload: $($payload.FullName)"
& 7z x $payload.FullName "-o$baseDir" -y | Out-Host
if($LASTEXITCODE -ne 0){throw 'Could not extract the original visual runtime payload.'}

$appExe=Get-ChildItem $baseDir -File -Filter '*.exe'|Where-Object{$_.Name -notmatch 'Uninstall'}|Select-Object -First 1
$appAsar=Join-Path $baseDir 'resources\app.asar'
if(-not $appExe){throw 'Original Player executable was not found in the extracted payload.'}
if(-not(Test-Path $appAsar)){throw 'Original Player app.asar was not found in the extracted payload.'}
if($appExe.Name -ne 'Gold Slots Player.exe'){
  Copy-Item $appExe.FullName (Join-Path $baseDir 'Gold Slots Player.exe') -Force
}
Get-ChildItem $baseDir -Recurse -File|Select-Object FullName,Length|Format-Table -AutoSize|Out-String -Width 300|Out-File 'work/BASE-RUNTIME-FILES.txt' -Encoding utf8
Write-Host "Original visual runtime extracted to $baseDir"
