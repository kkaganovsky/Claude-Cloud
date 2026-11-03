# GraftSeg installer for Windows (NVIDIA GPU optional). Run via install.bat, or:
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-NewEnv]
param([switch]$NewEnv)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvDir = if ($env:GRAFTSEG_ENV) { $env:GRAFTSEG_ENV } else { Join-Path $HOME "graftseg-env" }
function Say($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

$py = (Get-Command py -ErrorAction SilentlyContinue)
$Python = if ($py) { "py" } else { "python" }
& $Python -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Python 3.10+ is needed: install it from https://www.python.org (tick 'Add to PATH')." -ForegroundColor Red; exit 1 }

& $Python -c "import nnunetv2, torch" 2>$null
if (-not $NewEnv -and $LASTEXITCODE -eq 0) {
    Say "nnU-Net and PyTorch already installed - installing GraftSeg there (your torch is kept)"
    & $Python -m pip install -e $Here
    $Exe = (& $Python -c "import sys; print(sys.executable)")
} else {
    Say "Creating environment $EnvDir"
    & $Python -m venv $EnvDir
    $Exe = Join-Path $EnvDir "Scripts\python.exe"
    & $Exe -m pip install --upgrade pip wheel
    $index = "https://download.pytorch.org/whl/cpu"
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $line = (& nvidia-smi | Select-String "CUDA Version:\s*([0-9]+)\.([0-9]+)").Matches
        if ($line) {
            $v = [int]($line[0].Groups[1].Value + $line[0].Groups[2].Value)
            Say "NVIDIA driver supports CUDA up to $($line[0].Groups[1].Value).$($line[0].Groups[2].Value)"
            foreach ($c in 128, 126, 124, 121, 118) { if ($v -ge $c) { $index = "https://download.pytorch.org/whl/cu$c"; break } }
        }
    } else { Say "No NVIDIA GPU found: CPU PyTorch (segmenting works, slower; training needs a GPU)" }
    & $Exe -m pip install torch --index-url $index
    if ($LASTEXITCODE -ne 0) { Say "Falling back to the default PyTorch build"; & $Exe -m pip install torch }
    & $Exe -m pip install -e $Here
}
& $Exe -m graftseg install-desktop
Say "Checking the installation"
& $Exe -m graftseg doctor
Say "Done. Start GraftSeg from the desktop shortcut (GraftSeg.bat) or:  `"$Exe`" -m graftseg"
