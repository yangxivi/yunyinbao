; ============================================
; 云印宝 NSIS 安装脚本
; ============================================
Unicode true

Name "云印宝"
Caption "云印宝 安装程序"
OutFile "yunyinbao_setup_tmp.exe"
InstallDir "$LOCALAPPDATA\Programs\云印宝"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUnInstDetails hide

!include "MUI2.nsh"
!include "Sections.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; ---- MUI Settings ----
!define MUI_ABORTWARNING
!define MUI_ICON "server\F.ico"
!define MUI_UNICON "server\F.ico"

; ---- Pages ----
!define MUI_PAGE_CUSTOMFUNCTION_PRE "MUI_PAGE_DIRECTORY_PRE"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

; ---- Language ----
!insertmacro MUI_LANGUAGE "SimpChinese"

; ---- Reserve Files ----
ReserveFile /plugin nsExec.dll

; ============================================
; 强制锁定安装目录到 %LOCALAPPDATA%\Programs\云印宝
; （不依赖用户输入，避免手抖改到 Program Files 触发权限错误）
; ============================================
Function MUI_PAGE_DIRECTORY_PRE
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\云印宝"
FunctionEnd

; ============================================
; Installer Sections
; ============================================

Section "云印宝服务端（连接打印机的电脑）" SEC_SERVER
  SetOutPath "$INSTDIR\云印宝服务端"
  File "dist\云印宝服务端\云印宝服务端.exe"
  ; 用 File /r 不带 \*.* 保留 _internal 目录结构（带 \*.* 会把 _internal 里的所有文件平铺到 SetOutPath）
  File /r "dist\云印宝服务端\_internal"

  ; Desktop shortcut
  CreateShortCut "$DESKTOP\云印宝服务端.lnk" \
    "$INSTDIR\云印宝服务端\云印宝服务端.exe" "" \
    "$INSTDIR\云印宝服务端\_internal\server\F.ico" 0

  ; Start menu shortcut
  CreateShortCut "$SMPROGRAMS\云印宝\云印宝服务端.lnk" \
    "$INSTDIR\云印宝服务端\云印宝服务端.exe" "" \
    "$INSTDIR\云印宝服务端\_internal\server\F.ico" 0
SectionEnd

Section "云印宝客户端（远程打印设备）" SEC_CLIENT
  SetOutPath "$INSTDIR\云印宝客户端"
  File "dist\云印宝客户端\云印宝客户端.exe"
  ; 用 File /r 不带 \*.* 保留 _internal 目录结构
  File /r "dist\云印宝客户端\_internal"

  CreateShortCut "$DESKTOP\云印宝客户端.lnk" \
    "$INSTDIR\云印宝客户端\云印宝客户端.exe" "" \
    "$INSTDIR\云印宝客户端\_internal\client\K.ico" 0

  CreateShortCut "$SMPROGRAMS\云印宝\云印宝客户端.lnk" \
    "$INSTDIR\云印宝客户端\云印宝客户端.exe" "" \
    "$INSTDIR\云印宝客户端\_internal\client\K.ico" 0
SectionEnd

Section "-PostInstall" ; Hidden
  SetOutPath "$INSTDIR"

  ; Ensure start menu folder exists
  CreateDirectory "$SMPROGRAMS\云印宝"

  ; Uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Start menu uninstall shortcut
  CreateShortCut "$SMPROGRAMS\云印宝\卸载云印宝.lnk" \
    "$INSTDIR\Uninstall.exe" "" \
    "$INSTDIR\云印宝服务端\_internal\server\F.ico" 0

  ; Registry - install dir
  WriteRegStr HKCU "Software\云印宝" "InstallDir" "$INSTDIR"

  ; Add/Remove Programs entry
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "DisplayName" "云印宝"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "DisplayIcon" '"$INSTDIR\云印宝服务端\_internal\server\F.ico"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "DisplayVersion" "v9"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "Publisher" "曦微"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "NoRepair" 1

  ; Calculate estimated size
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" \
    "EstimatedSize" "$0"
SectionEnd

; ============================================
; Component Descriptions
; ============================================
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_SERVER} "安装在直接连接打印机的电脑上，提供打印服务。"
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_CLIENT} "安装在需要远程提交打印任务的设备上。"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ============================================
; .onInit - default selections
; ============================================
Function .onInit
  ; 强制锁定到 LOCALAPPDATA（重装时不记忆上次路径）
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\云印宝"
  ; 旧版卸不干净（含残留 _internal 散落文件）—— 重装前先调旧卸载器清理一次
  SetRegView default
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝" "UninstallString"
  ${If} $0 != ""
    ; 静默调用旧卸载器（不等它完成，下面 SECTION 还会再 RMDir 兜底）
    ExecWait '$0 /S _?=$INSTDIR'
  ${EndIf}
  ; Both selected by default
  SectionSetFlags ${SEC_SERVER} ${SF_SELECTED}
  SectionSetFlags ${SEC_CLIENT} ${SF_SELECTED}
FunctionEnd

; ============================================
; Uninstaller Section
; ============================================
Section "Uninstall"
  ; Kill running processes first
  nsExec::ExecToLog 'taskkill /F /IM 云印宝服务端.exe'
  nsExec::ExecToLog 'taskkill /F /IM 云印宝客户端.exe'
  Sleep 500

  ; Remove server files - 整目录强删，避免旧版 _internal 内容散落在根目录导致卸不干净
  RMDir /r /REBOOTOK "$INSTDIR\云印宝服务端"

  ; Remove client files - 整目录强删
  RMDir /r /REBOOTOK "$INSTDIR\云印宝客户端"

  ; Remove shortcuts
  Delete "$DESKTOP\云印宝服务端.lnk"
  Delete "$DESKTOP\云印宝客户端.lnk"
  Delete "$SMPROGRAMS\云印宝\云印宝服务端.lnk"
  Delete "$SMPROGRAMS\云印宝\云印宝客户端.lnk"
  Delete "$SMPROGRAMS\云印宝\卸载云印宝.lnk"
  RMDir "$SMPROGRAMS\云印宝"

  ; Remove uninstaller
  Delete /REBOOTOK "$INSTDIR\Uninstall.exe"

  ; Remove registry
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\云印宝"
  DeleteRegKey HKCU "Software\云印宝"

  ; Remove install dir (if empty)
  RMDir /REBOOTOK "$INSTDIR"
SectionEnd
