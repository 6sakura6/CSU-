param([string]$Python = '', [string]$OutputDirectory = 'dist/release-v1.2.2')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (-not $outputRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must stay inside this project.'
}
$applicationRoot = Join-Path $outputRoot '平安打卡助手'
if (Test-Path -LiteralPath $applicationRoot) {
    throw 'Output already exists. Choose a fresh -OutputDirectory; existing programs and data are never overwritten.'
}
if (-not $Python) {
    $Python = Join-Path $projectRoot 'work/lite-venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        $Python = Join-Path (Split-Path -Parent $projectRoot) 'work/lite-venv/Scripts/python.exe'
    }
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'Create work/lite-venv, install source/requirements.txt, or pass -Python. A shared parent workspace environment is also supported.' }
$buildRoot = Join-Path $projectRoot 'build/full-lite-build'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$arguments = @('-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
    '--name','平安打卡助手','--distpath',$outputRoot,'--workpath',$buildRoot,'--specpath',$buildRoot,
    '--paths',$PSScriptRoot,'--icon',(Join-Path $PSScriptRoot 'assets/app.ico'),
    '--add-data',"$(Join-Path $PSScriptRoot 'lite_ui');lite_ui",
    '--add-data',"$(Join-Path $PSScriptRoot 'assets/app.ico');lite-assets/assets",
    '--add-data',"$(Join-Path $PSScriptRoot 'ui/assets');lite-assets/ui/assets")
foreach ($module in @('PySide6','PyQt5','PyQt6','qtpy','playwright','numpy','PIL','tkinter',
    'cefpython3','webview.platforms.qt','webview.platforms.cef','webview.platforms.mshtml',
    'webview.platforms.gtk','webview.platforms.cocoa','gi','Cocoa','objc','wx')) {
    $arguments += @('--exclude-module',$module)
}
$arguments += (Join-Path $PSScriptRoot 'lite_app.py')
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Lightweight application build failed.' }
foreach ($file in @('README.md','使用说明.md','验证记录.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination (Join-Path $applicationRoot $file)
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination $applicationRoot
& $Python (Join-Path $projectRoot 'prototype/lite/collect_licenses.py') $applicationRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not collect dependency license notices.' }
foreach ($license in @('Microsoft.Web.WebView2.LICENSE.md','proxy_tools-LICENSE.txt')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "assets/$license") -Destination (Join-Path $applicationRoot 'licenses')
}
& $Python (Join-Path $projectRoot 'prototype/lite/measure.py') $applicationRoot
if ($LASTEXITCODE -ne 0) { throw 'The complete application exceeds the size/dependency gate.' }
Write-Output (Join-Path $applicationRoot '平安打卡助手.exe')
