#define MyAppName "StockWatcher"
#define MyAppVersion "0.3.0-alpha"
#define MyAppPublisher "StockWatcher"
#define MyAppExeName "StockWatcher.exe"

[Setup]
AppId={{A768F76E-58CC-42C8-AC00-793593DF88A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\..\dist\installer
OutputBaseFilename=StockWatcher-0.3.0-alpha-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\..\dist\StockWatcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--provider tdxquant"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--provider tdxquant"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--provider tdxquant"; Description: "启动 StockWatcher"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('运行数据和报告保留在 %LOCALAPPDATA%\StockWatcher。如需删除，请先确认已备份，再由用户手动处理。', mbInformation, MB_OK);
end;
