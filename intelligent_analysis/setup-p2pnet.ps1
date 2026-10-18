param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.13 -m venv .venv
        } else {
            & python -c "import sys; assert sys.version_info[:2] == (3,13), 'Install Python 3.13 first'"
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 is required' }
            & python -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.13 environment' }
    }
    function Invoke-CheckedPython {
        param([string[]]$Arguments)
        & $venvPython @Arguments
        if ($LASTEXITCODE -ne 0) { throw ('Python step failed: ' + ($Arguments -join ' ')) }
    }
    Invoke-CheckedPython -Arguments @('-c', "import sys; assert sys.version_info[:2] == (3,13), 'Use Python 3.13'")
    if (-not $SkipInstall) {
        Invoke-CheckedPython -Arguments @('-m', 'pip', 'install', 'torch>=2.6', 'torchvision>=0.21', '--index-url', 'https://download.pytorch.org/whl/cpu')
        Invoke-CheckedPython -Arguments @('-m', 'pip', 'install', '-e', '.[vision]')
    }
    Invoke-CheckedPython -Arguments @('-X', 'utf8', 'demo/verify_p2pnet_bundle.py', '--output', 'artifacts/clone-and-run/model-check.json')
    Invoke-CheckedPython -Arguments @('-X', 'utf8', 'demo/check_p2pnet.py', '--output', 'artifacts/clone-and-run/video-check.json')
    Write-Host 'P2PNet CPU ready. Reports: artifacts/clone-and-run/'
} finally {
    Pop-Location
}
