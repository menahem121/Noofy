!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Closing any running Noofy instance before installation..."
  ExecWait '"$SYSDIR\taskkill.exe" /IM noofy.exe /T /F'
!macroend
