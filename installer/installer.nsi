; =============================================================================
;  installer.nsi — NSIS Installer Script
;  Nois Floor Meter — F8AOF
;  Génère : NoisFloorMeter_Setup.exe
; =============================================================================

!include "MUI2.nsh"
!include "FileFunc.nsh"

; ── Infos application ──
!define APP_NAME        "Nois Floor Meter"
!define APP_VERSION     "1.0.0"
!define APP_AUTHOR      "F8AOF"
!define APP_EXE         "NoisFloorMeter.exe"
!define APP_ICON        "nfm.ico"
!define INSTALL_DIR     "$PROGRAMFILES64\NoisFloorMeter"
!define UNINSTALL_KEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\NoisFloorMeter"
!define PUBLISHER       "F8AOF Amateur Radio"
!define URL_INFO        "https://github.com/f8aof/nois-floor-meter"

; ── Métadonnées installeur ──
Name                    "${APP_NAME} ${APP_VERSION}"
OutFile                 "NoisFloorMeter_Setup.exe"
InstallDir              "${INSTALL_DIR}"
InstallDirRegKey        HKLM "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel   admin
BrandingText            "${APP_NAME} ${APP_VERSION} — ${APP_AUTHOR}"

; ── Interface MUI2 ──
!define MUI_ABORTWARNING
;!define MUI_ICON                    "assets\nfm.ico"
;!define MUI_UNICON                  "assets\nfm.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_BGCOLOR                 "0E1117"
!define MUI_TEXTCOLOR               "C9D6E8"
!define MUI_WELCOMEFINISHPAGE_BITMAP_NOSTRETCH

; ── Pages d'installation ──
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE       "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; ── Pages de désinstallation ──
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "French"

; =============================================================================
;  SECTION INSTALLATION
; =============================================================================
Section "Application principale" SecMain
    SectionIn RO  ; Obligatoire

    SetOutPath "$INSTDIR"

    ; Copier l'exécutable principal
    File "NoisFloorMeter.exe"
    ;File "assets\nfm.ico"
    File "LICENSE.txt"
    File "README.txt"

    ; ── Raccourci Menu Démarrer ──
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortcut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
                    "$INSTDIR\${APP_EXE}" \
                    "" \
                    "$INSTDIR\nfm.ico" 0 \
                    SW_SHOWNORMAL \
                    "" \
                    "Mesure du plancher de bruit RF — F8AOF"
    CreateShortcut  "$SMPROGRAMS\${APP_NAME}\Désinstaller.lnk" \
                    "$INSTDIR\Uninstall.exe"

    ; ── Raccourci Bureau ──
    CreateShortcut  "$DESKTOP\${APP_NAME}.lnk" \
                    "$INSTDIR\${APP_EXE}" \
                    "" \
                    "$INSTDIR\nfm.ico" 0

    ; ── Clé de registre désinstallation ──
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayName"      "${APP_NAME}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayVersion"   "${APP_VERSION}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "Publisher"        "${PUBLISHER}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "URLInfoAbout"     "${URL_INFO}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "InstallLocation"  "$INSTDIR"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "UninstallString"  "$INSTDIR\Uninstall.exe"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayIcon"      "$INSTDIR\nfm.ico"
    WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify"         1
    WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair"         1

    ; Taille estimée
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${UNINSTALL_KEY}" "EstimatedSize" "$0"

    ; Désinstalleur
    WriteUninstaller "$INSTDIR\Uninstall.exe"

SectionEnd

; =============================================================================
;  SECTION DÉSINSTALLATION
; =============================================================================
Section "Uninstall"

    ; Supprimer les fichiers
    Delete "$INSTDIR\NoisFloorMeter.exe"
    Delete "$INSTDIR\nfm.ico"
    Delete "$INSTDIR\LICENSE.txt"
    Delete "$INSTDIR\README.txt"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir  "$INSTDIR"

    ; Supprimer raccourcis
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\Désinstaller.lnk"
    RMDir  "$SMPROGRAMS\${APP_NAME}"
    Delete "$DESKTOP\${APP_NAME}.lnk"

    ; Supprimer clé registre
    DeleteRegKey HKLM "${UNINSTALL_KEY}"

    MessageBox MB_OK "$(^Name) a été désinstallé avec succès."

SectionEnd
