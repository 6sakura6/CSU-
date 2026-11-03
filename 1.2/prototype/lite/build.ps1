param(
    [string]$OutputDirectory = 'dist/lite-backend-v0.2.0',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (-not $outputRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Prototype output must remain inside this project.'
}
$applicationRoot = Join-Path $outputRoot 'CSUCheckinLitePrototype'
if (Test-Path -LiteralPath $applicationRoot) {
    throw 'Output already exists. Choose a fresh -OutputDirectory; existing programs and data are never overwritten.'
}
if (-not $Python) {
    $Python = Join-Path $projectRoot 'work/lite-venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        $Python = Join-Path (Split-Path -Parent $projectRoot) 'work/lite-venv/Scripts/python.exe'
    }
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'Create work/lite-venv, install prototype/lite/requirements.txt, or pass -Python. A shared parent workspace environment is also supported.' }
$buildRoot = Join-Path $projectRoot 'build/lite-build'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$sourceRoot = Join-Path $projectRoot 'source'
$arguments = @('-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
    '--name','CSUCheckinLitePrototype','--distpath',$outputRoot,'--workpath',$buildRoot,'--specpath',$buildRoot,
    '--paths',$sourceRoot,'--icon',(Join-Path $sourceRoot 'assets/app.ico'),
    '--add-data',"$(Join-Path $PSScriptRoot 'ui');ui",
    '--add-data',"$(Join-Path $sourceRoot 'assets/app.ico');lite-assets/assets",
    '--add-data',"$(Join-Path $sourceRoot 'ui/assets');lite-assets/ui/assets",
    '--add-data',"$(Join-Path $sourceRoot 'THIRD_PARTY_NOTICES.md');lite-assets")
foreach ($module in @('PySide6','PyQt5','PyQt6','qtpy','playwright','numpy','PIL','tkinter',
    'cefpython3','webview.platforms.qt','webview.platforms.cef','webview.platforms.mshtml',
    'webview.platforms.gtk','webview.platforms.cocoa','gi','Cocoa','objc','wx')) {
    $arguments += @('--exclude-module',$module)
}
$arguments += (Join-Path $PSScriptRoot 'main.py')
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Prototype build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $applicationRoot '验证版说明.md')
& $Python (Join-Path $PSScriptRoot 'collect_licenses.py') $applicationRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not collect dependency license notices.' }
& $Python (Join-Path $PSScriptRoot 'measure.py') $applicationRoot
if ($LASTEXITCODE -ne 0) { throw 'Prototype does not satisfy the size or dependency gate.' }
Write-Output (Join-Path $applicationRoot 'CSUCheckinLitePrototype.exe')
