#define MyAppName "StockWatcher"
#define MyAppVersion "0.6.0-alpha.5"
#define MyAppPublisher "StockWatcher"
#define MyAppExeName "StockWatcher.exe"
#define MyAppIcon "..\..\src\stock_watcher\ui\assets\stockwatcher.ico"
#ifndef StockWatcherBundleDir
  #define StockWatcherBundleDir "..\..\dist\StockWatcher"
#endif
#ifndef StockWatcherOutputDir
  #define StockWatcherOutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{A768F76E-58CC-42C8-AC00-793593DF88A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
OutputDir={#StockWatcherOutputDir}
OutputBaseFilename=StockWatcher-0.6.0-alpha.5-setup
Compression=lzma2
SolidCompression=yes
SetupIconFile={#MyAppIcon}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "{#StockWatcherBundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 StockWatcher"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('运行数据和报告保留在 %LOCALAPPDATA%\StockWatcher。如需删除，请先确认已备份，再由用户手动处理。', mbInformation, MB_OK);
end;
