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
; AIProductionStudio.spec's EXE() names the output 'AIProductionStudio',
; not 'main.exe' -- every reference below now matches what PyInstaller
; actually produces.
UninstallDisplayIcon={app}\AIProductionStudio.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\AIProductionStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\AI Production Studio"; Filename: "{app}\AIProductionStudio.exe"
Name: "{commondesktop}\AI Production Studio"; Filename: "{app}\AIProductionStudio.exe"

; NOTE: there used to be a [Run] entry here launching "{app}\setup.exe" as a
; post-install first-run wizard. That file never existed -- setup.py (the
; script it presumably meant) does `pip install`, GPU prompts, and an
; interactive model-provider wizard, none of which work inside a frozen
; PyInstaller exe (no real pip/interpreter environment to install into).
; It's a from-source-only workflow (`python setup.py`), not something that
; can just be compiled into setup.exe as-is.
;
; This app still needs SOME first-run flow once packaged -- e.g. main.py
; detecting a missing config.yaml and showing an in-app setup dialog
; (PyQt6, since main.py already has PyQt6.QtMultimedia etc. available) --
; but that's an actual application design decision, not something to
; silently invent here. Until that exists, a fresh install has no
; config.yaml and whatever main.py currently does when one is missing.

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  Result := True;
end;
