; BlazEng — Inno Setup Windows installer
;
; Build (after PyInstaller has produced dist\BlazEng\):
;     ISCC.exe /DAppVersion=0.9.1 installer.iss
; AppVersion defaults to the value below when not passed on the command line.
; Output: dist\installer\BlazEng_Setup_<version>.exe
;
; What gets installed: the whole PyInstaller folder (BlazEng.exe = desktop app,
; blazeng-cli.exe = command line, plus bundled ffmpeg/Godot). Everything the app
; WRITES (settings, projects, logs) goes to %LOCALAPPDATA%\BlazEng at run time,
; never into the install folder - see src/paths.py.

#ifndef AppVersion
  #define AppVersion "0.11.0"
#endif
#define AppName      "BlazEng"
#define AppExe       "BlazEng.exe"
#define AppPublisher "BlazEng"
#define AppURL       "https://github.com/MrGrimJoe/BlazEng"

[Setup]
; Fixed AppId so a newer installer recognises and upgrades an existing install
; (without it Inno derives identity from the name, and renaming breaks upgrades).
; Never change this value once released.
AppId={{A89DDE7C-E498-4B17-82EF-0B5837478E2E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=dist\installer
OutputBaseFilename=BlazEng_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installs for all users by default (Program Files); the dialog lets a user without
; admin rights choose a per-user install instead.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; Offer to close a running BlazEng instead of failing on locked files.
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExe}
SetupLogging=yes
; assets\icon.ico isn't in the repo yet; only set it if present so a missing design
; asset can't break compilation (Inno uses its default icon otherwise).
#if FileExists("assets\icon.ico")
SetupIconFile=assets\icon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Optional, and off by default: a desktop icon should be the user's choice.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; PyInstaller folders change between versions; remove the old runtime first so
; stale libraries from a previous release can't shadow the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\BlazEng\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; No first-run wizard is launched here on purpose: the app itself opens its
; settings dialog on first start when no provider is configured.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Uninstall removes the program but keeps the user's settings, projects and
// generated videos unless they say otherwise. Silent uninstalls always keep them.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) then
      if SuppressibleMsgBox(
           'Also delete your BlazEng settings, projects and generated videos?' + #13#10 + #13#10 + DataDir,
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
