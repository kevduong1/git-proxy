# Launch git-proxy on Windows (PowerShell). Opens the GUI when run without arguments.
#   .\git-proxy.ps1              -> GUI
#   .\git-proxy.ps1 sync <pair> to-mirror
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git was not found on PATH. Install Git for Windows: https://git-scm.com/download/win"
    exit 1
}

$py = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break }
}
if (-not $py) {
    Write-Error "Python 3 was not found. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
    exit 1
}

if ($py -eq "py") {
    & py -3 (Join-Path $root "git-proxy.py") @args
} else {
    & $py (Join-Path $root "git-proxy.py") @args
}
exit $LASTEXITCODE
