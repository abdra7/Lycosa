; Inno Setup script for the Lycosa desktop dashboard.
; Built in CI with:  iscc /DAppVersion=<version> lycosa.iss
; Expects a completed `flutter build windows --release` two levels up.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7E2C9B4A-52F3-4E1B-9C8D-A1B0C3D4E5F6}
AppName=Lycosa
AppVersion={#AppVersion}
AppPublisher=Lycosa
AppPublisherURL=https://github.com/abdra7/Lycosa
DefaultDirName={autopf}\Lycosa
DefaultGroupName=Lycosa
; per-user install by default, no admin required; elevation offered if chosen
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=Lycosa-windows-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Lycosa

[Tasks]
Name: desktopicon; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: firewallrule; Description: "Allow Lycosa through Windows Firewall for LAN device discovery (recommended)"; GroupDescription: "Network:"

[Files]
Source: "..\..\build\windows\x64\runner\Release\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Lycosa"; Filename: "{app}\lycosa_dashboard.exe"
Name: "{autodesktop}\Lycosa"; Filename: "{app}\lycosa_dashboard.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\lycosa_dashboard.exe"; Description: "Launch Lycosa"; Flags: nowait postinstall skipifsilent

[Code]
// mDNS discovery replies arrive on UDP 5353; Windows Firewall drops them by
// default regardless of network profile (Public/Private) unless an inbound
// allow rule exists. Scoped to "any" profile so a user on a Public-classified
// network (the common default for new connections) isn't left broken.
procedure AddDiscoveryFirewallRule;
var
  ResultCode: Integer;
begin
  ShellExec('runas', 'netsh.exe',
    'advfirewall firewall add rule name="Lycosa Dashboard (mDNS discovery)" ' +
    'dir=in action=allow protocol=UDP localport=5353 profile=any',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and IsTaskSelected('firewallrule') then
    AddDiscoveryFirewallRule;
end;
