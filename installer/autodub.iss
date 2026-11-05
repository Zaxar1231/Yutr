; Inno Setup 6 script for Автодубляж.
; Build: iscc /DAppVersion=0.1.0 /DStageDir=..\build\stage /DOutputDir=..\dist autodub.iss
; The stage folder is produced by scripts/build_release.py.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\build\stage"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#define AppName "Автодубляж"
#define AppId "{{6F3B2C1E-8D4A-4B7E-9A51-2C0D9E7F4A10}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Zaxar1231
AppPublisherURL=https://github.com/Zaxar1231/Yutr
AppSupportURL=https://github.com/Zaxar1231/Yutr/issues
AppUpdatesURL=https://github.com/Zaxar1231/Yutr/releases
; Per-user install into %LOCALAPPDATA%\Programs, no administrator rights needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\Autodub
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Autodub-Setup-{#AppVersion}
SetupIconFile=..\docs\assets\icon.ico
UninstallDisplayIcon={app}\icon.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=yes
ShowLanguageDialog=no
LicenseFile=..\LICENSE

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Значки:"
Name: "portable"; Description: "Портативный режим: модели, настройки и кэш хранить в папке программы"; GroupDescription: "Режим:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\docs\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\uvw.exe"; Parameters: "--config-file ""{app}\uv.toml"" run --python 3.12 --no-project --gui-script ""{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\uvw.exe"; Parameters: "--config-file ""{app}\uv.toml"" run --python 3.12 --no-project --gui-script ""{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\uvw.exe"; Parameters: "--config-file ""{app}\uv.toml"" run --python 3.12 --no-project --gui-script ""{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Запустить {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\versions"
Type: filesandordirs; Name: "{app}\downloads"
Type: files; Name: "{app}\current"
Type: files; Name: "{app}\previous"
Type: files; Name: "{app}\bad.json"

[Code]
function DataDir(): String;
begin
  if FileExists(ExpandConstant('{app}\portable.flag')) then
    Result := ExpandConstant('{app}\data')
  else
    Result := ExpandConstant('{localappdata}\Autodub');
end;

procedure WriteUvConfig();
var
  Dir, Cfg: String;
begin
  Dir := DataDir();
  StringChangeEx(Dir, '\', '\\', True);
  { uv keeps its Python and cache inside our data folder: the system Python is never touched. }
  Cfg := 'python-install-dir = "' + Dir + '\\python"' + #13#10 +
         'cache-dir = "' + Dir + '\\cache\\uv"' + #13#10 +
         'python-preference = "only-managed"' + #13#10;
  SaveStringToFile(ExpandConstant('{app}\uv.toml'), Cfg, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('portable') then
      SaveStringToFile(ExpandConstant('{app}\portable.flag'), 'portable', False);
    { The version installed by this setup becomes current; older versions stay as backups. }
    SaveStringToFile(ExpandConstant('{app}\current'), '{#AppVersion}', False);
    WriteUvConfig();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Dir := DataDir();
    if DirExists(Dir) then
      if MsgBox('Удалить также скачанные модели, настройки и кэш (' + Dir + ')?' + #13#10 +
                'Если вы планируете установить программу снова, лучше оставить — не придётся качать заново.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dir, True, True, True);
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
