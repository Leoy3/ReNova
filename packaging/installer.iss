; Instalador de ReNova (Inno Setup 6.3+). Lo compila tools\build.py.
; Instala por usuario (no pide permisos de administrador) en %LOCALAPPDATA%\Programs\ReNova.

#ifndef AppVersion
  #define AppVersion "2.4.5"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\ReNova"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{8F3C2A51-6E2B-4C1D-9B7A-5E0F4D2C1A90}
AppName=ReNova
AppVersion={#AppVersion}
AppVerName=ReNova {#AppVersion}
AppPublisher=ReNova
AppComments=Descargador y conversor de video (H.264 compatible con Premiere)
DefaultDirName={autopf}\ReNova
DefaultGroupName=ReNova
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutDir}
OutputBaseFilename=ReNova-Setup-{#AppVersion}
SetupIconFile=..\assets\renova.ico
UninstallDisplayIcon={app}\ReNova.exe
UninstallDisplayName=ReNova
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=2
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador de ReNova

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Evita mezclar archivos de una versión anterior
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ReNova"; Filename: "{app}\ReNova.exe"
Name: "{autodesktop}\ReNova"; Filename: "{app}\ReNova.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ReNova.exe"; Description: "{cm:LaunchProgram,ReNova}"; Flags: nowait postinstall skipifsilent
; Actualización lanzada desde ReNova (/SILENT /relaunch=1): vuelve a abrir el programa al terminar
Filename: "{app}\ReNova.exe"; Flags: nowait; Check: ShouldRelaunch

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;
