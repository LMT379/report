# Finds Python 3, installs it for the current user when it is missing, then runs the report.
$ErrorActionPreference = "Continue"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReportScript = Join-Path $Root "sitespeed_cn.py"
$PythonVersion = "3.14.7"

function Test-PythonExe {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    if ($Exe -match '\\WindowsApps\\') { return $false }
    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    $item = Get-Item -LiteralPath $Exe -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -lt 1000) { return $false }
    & $Exe -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
    return $LASTEXITCODE -eq 0
}

function Get-RegistryPythons {
    $found = @()
    foreach ($hive in @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\WOW6432Node\Python\PythonCore"
    )) {
        if (-not (Test-Path -LiteralPath $hive)) { continue }
        $versions = Get-ChildItem -LiteralPath $hive -ErrorAction SilentlyContinue |
            Sort-Object { $_.PSChildName } -Descending
        foreach ($version in $versions) {
            $key = Join-Path $version.PSPath "InstallPath"
            if (-not (Test-Path -LiteralPath $key)) { continue }
            $props = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
            if ($props.ExecutablePath) { $found += $props.ExecutablePath }
        }
    }
    return $found
}

function Get-FolderPythons {
    $found = @()
    $bins = @(
        (Join-Path $env:LOCALAPPDATA "Python\bin\python.exe")
    )
    foreach ($bin in $bins) {
        if (Test-Path -LiteralPath $bin) { $found += $bin }
    }
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Python"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path $env:ProgramFiles "Python")
    )
    if (${env:ProgramFiles(x86)}) {
        $roots += (Join-Path ${env:ProgramFiles(x86)} "Python")
    }
    foreach ($dir in $roots) {
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        $exes = Get-ChildItem -LiteralPath $dir -Filter "python.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue
        foreach ($exe in $exes) { $found += $exe.FullName }
    }
    return $found
}

function Find-Python {
    # Keep these as arrays. A single path plus an array would be glued into one string.
    $candidates = @()
    $candidates += @(Get-FolderPythons)
    $candidates += @(Get-RegistryPythons)
    $seen = @{}
    foreach ($exe in $candidates) {
        if (-not $exe -or $seen.ContainsKey($exe)) { continue }
        $seen[$exe] = $true
        if (Test-PythonExe $exe) { return $exe }
    }
    return $null
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$user;$machine"
}

function Install-PythonWithWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    Write-Host "正在用 winget 安装 Python $PythonVersion ……"
    & winget install --id Python.Python.3.14 --exact --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    return $LASTEXITCODE -eq 0
}

function Install-PythonFromWeb {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -eq "ARM64") {
        $file = "python-$PythonVersion-arm64.exe"
    } elseif ($arch -eq "X86") {
        $file = "python-$PythonVersion.exe"
    } else {
        $file = "python-$PythonVersion-amd64.exe"
    }
    $url = "https://www.python.org/ftp/python/$PythonVersion/$file"
    $dest = Join-Path $env:TEMP $file
    Write-Host "正在从 python.org 下载 $file ……"
    & curl.exe -L --fail --retry 2 -o $dest $url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dest)) { return $false }
    Write-Host "正在安装，窗口可能停一会儿。"
    $proc = Start-Process -FilePath $dest -Wait -PassThru -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_test=0",
        "Include_launcher=1",
        "Include_tcltk=1",
        "Include_pip=1"
    )
    return $proc.ExitCode -eq 0
}

if (-not (Test-Path -LiteralPath $ReportScript)) {
    Write-Host "找不到 sitespeed_cn.py。请保持它和 report.bat 在同一个文件夹。"
    exit 1
}

$python = Find-Python
if (-not $python) {
    Write-Host "这台电脑还没有可用的 Python 3。开始安装到当前用户，一般不需要管理员密码，需要联网。"
    Install-PythonWithWinget | Out-Null
    Refresh-Path
    $python = Find-Python
}
if (-not $python) {
    Install-PythonFromWeb | Out-Null
    Refresh-Path
    $python = Find-Python
}
if (-not $python) {
    Write-Host "Python 没有装上。可以打开 https://www.python.org/downloads/ 手动安装 Python 3，然后重新双击 report.bat。"
    exit 1
}

& $python $ReportScript @args
exit $LASTEXITCODE
