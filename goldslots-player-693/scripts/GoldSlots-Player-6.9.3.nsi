Unicode True
Name "Gold Slots Player 6.9.3"
OutFile "..\release\GoldSlots-Player-Setup-6.9.3.exe"
InstallDir "$PROGRAMFILES64\Gold Slots Player"
InstallDirRegKey HKLM "Software\Gold Slots Player" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
SetCompressorDictSize 64
Icon "..\build\icon.ico"
UninstallIcon "..\build\icon.ico"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "6.9.3.0"
VIAddVersionKey /LANG=1033 "ProductName" "Gold Slots Player"
VIAddVersionKey /LANG=1033 "ProductVersion" "6.9.3"
VIAddVersionKey /LANG=1033 "FileDescription" "Gold Slots Player Original Premium Visual Installer"
VIAddVersionKey /LANG=1033 "FileVersion" "6.9.3"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Gold Slots"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Gold Slots Player" SEC_MAIN
  SetShellVarContext all
  SetOutPath "$INSTDIR"
  File /r "..\work\prepackaged\*.*"
  WriteRegStr HKLM "Software\Gold Slots Player" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "DisplayName" "Gold Slots Player 6.9.3"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "DisplayVersion" "6.9.3"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "Publisher" "Gold Slots"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "DisplayIcon" "$INSTDIR\Gold Slots Player.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "UninstallString" '"$INSTDIR\Uninstall Gold Slots Player.exe"'
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player" "NoRepair" 1
  WriteUninstaller "$INSTDIR\Uninstall Gold Slots Player.exe"
  CreateDirectory "$SMPROGRAMS\Gold Slots Player"
  CreateShortcut "$SMPROGRAMS\Gold Slots Player\Gold Slots Player.lnk" "$INSTDIR\Gold Slots Player.exe" "" "$INSTDIR\Gold Slots Player.exe" 0
  CreateShortcut "$SMPROGRAMS\Gold Slots Player\Uninstall Gold Slots Player.lnk" "$INSTDIR\Uninstall Gold Slots Player.exe"
  CreateShortcut "$DESKTOP\Gold Slots Player.lnk" "$INSTDIR\Gold Slots Player.exe" "" "$INSTDIR\Gold Slots Player.exe" 0
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  Delete "$DESKTOP\Gold Slots Player.lnk"
  Delete "$SMPROGRAMS\Gold Slots Player\Gold Slots Player.lnk"
  Delete "$SMPROGRAMS\Gold Slots Player\Uninstall Gold Slots Player.lnk"
  RMDir "$SMPROGRAMS\Gold Slots Player"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gold Slots Player"
  DeleteRegKey HKLM "Software\Gold Slots Player"
  RMDir /r "$INSTDIR"
SectionEnd
