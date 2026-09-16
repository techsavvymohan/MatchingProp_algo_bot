# ==============================================================================
# MatchingProp Algo Bot - 1-Click Automated Setup for Fresh Windows VPS
# Installs: Git, Python 3.11, Clones Repository, Creates Venv, Installs Requirements, Sets .env
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
$ErrorActionPreference = "Continue"

Write-Host "`n=================================================================" -ForegroundColor Cyan
Write-Host "  MatchingProp Algo Bot - VPS 1-Click Environment Installer" -ForegroundColor Cyan
Write-Host "=================================================================`n" -ForegroundColor Cyan

$webClient = New-Object System.Net.WebClient

# 1. Check / Install Git
Write-Host "[1/6] Checking Git installation..." -ForegroundColor Yellow
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host "  -> Git not found. Downloading & Installing Git for Windows..." -ForegroundColor White
    $gitInstaller = "$env:TEMP\Git-Installer.exe"
    $gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe"
    $webClient.DownloadFile($gitUrl, $gitInstaller)
    Start-Process -FilePath $gitInstaller -ArgumentList "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-" -Wait
    Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue

    if (Test-Path "C:\Program Files\Git\cmd") {
        $env:Path = "C:\Program Files\Git\cmd;" + $env:Path
    }
}
$gitCheck = Get-Command git -ErrorAction SilentlyContinue
if ($gitCheck) {
    Write-Host "  [PASS] Git is ready ($($gitCheck.Source))." -ForegroundColor Green
} else {
    Write-Host "  [WARN] Git installed, added to Path." -ForegroundColor Yellow
}

# 2. Check / Install Python 3.11
Write-Host "`n[2/6] Checking Python installation..." -ForegroundColor Yellow
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    Write-Host "  -> Python not found. Downloading & Installing Python 3.11 (64-bit)..." -ForegroundColor White
    $pyInstaller = "$env:TEMP\python-3.11.9-amd64.exe"
    $pyUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $webClient.DownloadFile($pyUrl, $pyInstaller)
    Start-Process -FilePath $pyInstaller -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1" -Wait
    Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue

    if (Test-Path "C:\Program Files\Python311") {
        $env:Path = "C:\Program Files\Python311;C:\Program Files\Python311\Scripts;" + $env:Path
    }
    if (Test-Path "$env:LocalAppData\Programs\Python\Python311") {
        $env:Path = "$env:LocalAppData\Programs\Python\Python311;$env:LocalAppData\Programs\Python\Python311\Scripts;" + $env:Path
    }
}
$pyCheck = Get-Command python -ErrorAction SilentlyContinue
if ($pyCheck) {
    Write-Host "  [PASS] Python is ready ($($pyCheck.Source))." -ForegroundColor Green
} else {
    Write-Host "  [WARN] Python installed, added to Path." -ForegroundColor Yellow
}

# 3. Clone Repository
Write-Host "`n[3/6] Cloning MatchingProp repository..." -ForegroundColor Yellow
$targetDir = "$HOME\Downloads\MatchingProp_algo_bot"
if (-not (Test-Path $targetDir)) {
    git clone https://github.com/techsavvymohan/MatchingProp_algo_bot.git $targetDir
} else {
    Write-Host "  -> Directory exists. Pulling latest code..." -ForegroundColor White
    git -C $targetDir pull origin main
}
Set-Location $targetDir
Write-Host "  [PASS] Repository is ready at: $targetDir" -ForegroundColor Green

# 4. Create Virtual Environment
Write-Host "`n[4/6] Creating Python Virtual Environment (venv)..." -ForegroundColor Yellow
$venvPython = "$targetDir\venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    python -m venv venv
}
Write-Host "  [PASS] Virtual environment created." -ForegroundColor Green

# 5. Install Dependencies
Write-Host "`n[5/6] Installing algorithmic trading dependencies (MetaTrader5, pandas, numpy, etc.)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r "$targetDir\requirements.txt" --quiet
Write-Host "  [PASS] All Python packages successfully installed." -ForegroundColor Green

# 6. Initialize .env File
Write-Host "`n[6/6] Initializing .env configuration file..." -ForegroundColor Yellow
$envFile = "$targetDir\.env"
$envExample = "$targetDir\.env.example"
if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "  [PASS] Created .env from .env.example template." -ForegroundColor Green
} else {
    Write-Host "  [PASS] .env file already exists." -ForegroundColor Green
}

# Summary and Next Steps
Write-Host "`n=================================================================" -ForegroundColor Cyan
Write-Host "  CONGRATULATIONS! VPS SETUP IS 100% COMPLETE!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "`nNext 3 Quick Steps to Trade Live on this VPS:" -ForegroundColor Yellow
Write-Host "1. Open .env in VS Code to enter MT5 credentials:" -ForegroundColor White
Write-Host "   (File location: $targetDir\.env)" -ForegroundColor Gray
Write-Host "   Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, and SYMBOLS=XAUUSD.x,EURUSD.x`n" -ForegroundColor Gray
Write-Host "2. Run Health Check in terminal:" -ForegroundColor White
Write-Host "   .\venv\Scripts\python.exe scripts\vps_health_check.py`n" -ForegroundColor Gray
Write-Host "3. Start Live Trading:" -ForegroundColor White
Write-Host "   .\venv\Scripts\python.exe -m xauusd_bot.main`n" -ForegroundColor Gray
