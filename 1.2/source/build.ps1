param([string]$Python = '', [string]$OutputDirectory = 'dist/release-v1.2.3', [switch]$LegacyQt)
$ErrorActionPreference = 'Stop'
if ($LegacyQt) {
    if (-not $Python) { $Python = 'python' }
    & (Join-Path $PSScriptRoot 'build_qt.ps1') -Python $Python -OutputDirectory $OutputDirectory
} else {
    & (Join-Path $PSScriptRoot 'build_lite.ps1') -Python $Python -OutputDirectory $OutputDirectory
}
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
