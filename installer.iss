; AI Production Studio — Inno Setup Windows Installer
; Builds a professional .exe installer

[Setup]
AppName=AI Production Studio
AppVersion=1.0.0
AppPublisher=AI Production Studio
DefaultDirName={autopf}\AI Production Studio
DefaultGroupName=AI Production Studio
OutputDir=dist\installer
OutputBaseFilename=AIProductionStudio_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\main.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\AIProductionStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\AI Production Studio"; Filename: "{app}\main.exe"
Name: "{commondesktop}\AI Production Studio"; Filename: "{app}\main.exe"

[Run]
Filename: "{app}\setup.exe"; Description: "Run first-time setup"; Flags: postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  Result := True;
end;
