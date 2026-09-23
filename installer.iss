; Inno Setup Script for FilmFreeway DeepSearch Pro
#define MyAppName "FilmFreeway DeepSearch Pro"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "FilmFreeway Intelligence"
#define MyAppExeName "FilmFreewayDeepSearch.exe"

[Setup]
; Unique application identifier
AppId={{9F7B182E-82C3-48FA-A4F7-1C3876B5A109}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userpf}\{#MyAppName}
DisableProgramGroupPage=yes
; Installs cleanly for the current user without administrator privileges
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=Setup_FilmFreeway_DeepSearch_v2
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Includes all compiled distribution files from PyInstaller
Source: "dist\FilmFreewayDeepSearch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[Code]
// Automatically close any running instances before installation begins
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM FilmFreewayDeepSearch.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
