param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
foreach ($target in @($buildRoot, $distRoot)) {
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output must remain inside the project directory.'
    }
}
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
& $Python -m PyInstaller --noconfirm --windowed --onedir `
    --name '平安打卡助手' --distpath $distRoot --workpath $buildRoot `
    --specpath $buildRoot (Join-Path $PSScriptRoot 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
Write-Output (Join-Path $distRoot '平安打卡助手\平安打卡助手.exe')
