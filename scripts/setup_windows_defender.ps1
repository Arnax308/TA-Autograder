# Run this script in an Elevated (Administrator) PowerShell window if Windows Security blocks unsigned student binaries:
# Right-click PowerShell -> Run as Administrator -> Execute this script

$appDir = Split-Path -Parent $PSScriptRoot
$devkitDir = Join-Path $env:USERPROFILE ".w64devkit"

Write-Host "Adding Windows Defender Exclusions..." -ForegroundColor Cyan

try {
    Add-MpPreference -ExclusionPath $appDir -ErrorAction Stop
    Write-Host " [OK] Added exclusion for: $appDir" -ForegroundColor Green
} catch {
    Write-Host " [!] Could not add exclusion for $appDir. Please run PowerShell as Administrator." -ForegroundColor Yellow
}

try {
    Add-MpPreference -ExclusionPath $devkitDir -ErrorAction Stop
    Write-Host " [OK] Added exclusion for: $devkitDir" -ForegroundColor Green
} catch {
    Write-Host " [!] Could not add exclusion for $devkitDir." -ForegroundColor Yellow
}

Write-Host "`nIf Smart App Control (SAC) is enabled on Windows 11:" -ForegroundColor Cyan
Write-Host "1. Open 'Windows Security' -> 'App & browser control' -> 'Smart App Control'"
Write-Host "2. Set Smart App Control to 'Off' or 'Evaluation'." -ForegroundColor Yellow
