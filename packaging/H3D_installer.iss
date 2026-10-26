; H3D 中小型文物三维建模自动化系统 —— Inno Setup 安装脚本
; 用法：iscc H3D_installer.iss（ISCC 7.x）
; 输出：Output/H3D-Setup-v{#MyAppVersion}.exe
;
; 2026-10-18 r7：合并此前并存的两份脚本（1.0.0 / 1.1.0 各一份，
;   改一份漏一份，导致装出来的仍是旧版本说明）。现在只有这一份。
;   源目录指向 release/H3D_v<版本>（含绿色版 exe），安装包与发行 zip 同源。
;
; 2026-10-23：版本号与源目录改为可注入（#ifndef 兜底 + ISCC /D 覆盖）。
;   此前两处写死 1.1.0/1.1.4，发版必漏改一处；现在统一由
;   scripts/build_installer.py 从 h3d.__version__ 注入。
; 编码要求：本文件必须 UTF-8 **带 BOM**，否则 Inno 按 ANSI 解析，
;   中文路径与文件名全部乱码、找不到文件。

#define MyAppName "H3D 中小型文物三维建模自动化系统"
#define MyAppExeName "H3D.exe"
; 以下两项由 scripts/build_installer.py 用 ISCC /D 注入（取自 h3d.__version__）。
; 保留 #ifndef 兜底值，方便手工直接跑 iscc 时也能编出版本正确的包。
#ifndef MyAppVersion
  #define MyAppVersion "1.1.4"
#endif
#ifndef MyAppDir
  #define MyAppDir "E:/h3d_lablab/release/H3D_v1.1.4"
#endif

[Setup]
AppId={{A7D3C2E5-9F41-4B6A-8C2E-0000000000A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
; 2026-10-18：不写这一项时属性页「文件版本」显示 X（Inno 默认占位），
; 用户无法从安装包本身确认版本。这里由 MyAppVersion 派生四段数值，
; 不会再出现「AppVersion 1.1.4、文件版本 1.1.0」这种自相矛盾。
VersionInfoVersion={#MyAppVersion}.0
; ProductVersion 默认由 AppVersion 推导（"1.1.0" 少一段），实测属性页会
; 显示成 "1.1.0X"；CompanyName 用中文时同样多一个尾字符（Inno 的
; 字节/字符长度混用）。这里都显式给四段数值与 ASCII 公司名，保持干净。
VersionInfoProductVersion={#MyAppVersion}.0
VersionInfoCompany=H3D Project
VersionInfoDescription=H3D 中小型文物三维建模自动化系统 安装程序
AppPublisher=文保数字化研发组
DefaultDirName={autopf}\H3D
DefaultGroupName=H3D 文物数字化
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=Output
OutputBaseFilename=H3D-Setup-v{#MyAppVersion}
SetupIconFile=..\assets\h3d_icon_v3.ico
LicenseFile=

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "{#MyAppDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppDir}\README_快速开始.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppDir}\VERSION.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppDir}\configs\*"; DestDir: "{app}\configs"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\使用说明（快速开始）"; Filename: "{app}\README_快速开始.txt"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 H3D"; Flags: nowait postinstall skipifsilent
