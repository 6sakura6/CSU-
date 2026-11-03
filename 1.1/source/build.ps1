param([string]$Python = 'python', [string]$OutputDirectory = 'dist')

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
foreach ($target in @($buildRoot, $distRoot)) {
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output must remain inside the project directory.'
    }
}
$applicationRoot = Join-Path $distRoot '平安打卡助手'
$applicationExe = Join-Path $applicationRoot '平安打卡助手.exe'
$runningTarget = Get-Process -Name '平安打卡助手' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $applicationExe }
if ($runningTarget -or (Test-Path -LiteralPath (Join-Path $applicationRoot 'data'))) {
    throw 'Build target is running or contains local data. Choose a clean -OutputDirectory.'
}
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
& $Python -m PyInstaller --noconfirm --windowed --onedir `
    --hidden-import pystray._win32 `
    --icon (Join-Path $PSScriptRoot 'assets\app.ico') `
    --add-data "$(Join-Path $PSScriptRoot 'assets\app.ico');assets" `
    --add-data "$(Join-Path $PSScriptRoot 'assets\app.png');assets" `
    --add-data "$(Join-Path $PSScriptRoot 'ui');ui" `
    --add-data "$(Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md');." `
    --copy-metadata PySide6 --copy-metadata PySide6_Essentials --copy-metadata shiboken6 `
    --name '平安打卡助手' --distpath $distRoot --workpath $buildRoot `
    --specpath $buildRoot (Join-Path $PSScriptRoot 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
# Qt 6.12 uses Windows ICU's unversioned API. A tool such as Poppler on
# PATH can make PyInstaller collect an incompatible ICU with the same name.
# Keep Windows' system ICU in use, as it is when running from source.
$bundledIcu = Join-Path $distRoot '平安打卡助手\_internal\icuuc.dll'
$systemIcu = Join-Path ([Environment]::GetFolderPath('System')) 'icuuc.dll'
if ((Test-Path -LiteralPath $bundledIcu) -and (Test-Path -LiteralPath $systemIcu)) {
    Move-Item -LiteralPath $bundledIcu -Destination (Join-Path $buildRoot 'excluded-icuuc.dll') -Force
}
Write-Output (Join-Path $distRoot '平安打卡助手\平安打卡助手.exe')
