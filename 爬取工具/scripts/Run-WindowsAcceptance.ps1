param(
  [Parameter(Mandatory=$true)][string]$Wheel,
  [Parameter(Mandatory=$true)][string]$Seed,
  [Parameter(Mandatory=$true)][string]$Output,
  [string]$Python = "py",
  [switch]$RequireMySql,
  [switch]$WithScheduler
)
$ErrorActionPreference = "Stop"
$Wheel = (Resolve-Path -LiteralPath $Wheel).Path
$Seed = (Resolve-Path -LiteralPath $Seed).Path
if (Test-Path -LiteralPath $Output) { throw "Output must be new; no files are removed" }
New-Item -ItemType Directory -Path $Output | Out-Null
$Output = (Resolve-Path -LiteralPath $Output).Path
$venv = Join-Path $Output "runtime"
if ((Split-Path -Leaf $Python) -match '^py(\.exe)?$') { & $Python -3.13 -m venv $venv } else { & $Python -m venv $venv }
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 venv failed" }
$interpreter = Join-Path $venv "Scripts/python.exe"
$wheelVersion = ([System.IO.Path]::GetFileName($Wheel) -split '-')[1]
$constraints = Join-Path $PSScriptRoot "../release/dependencies-$wheelVersion.txt"
if (Test-Path -LiteralPath $constraints) {
  & $interpreter -m pip install -c $constraints "${Wheel}[dev]"
} else {
  & $interpreter -m pip install "${Wheel}[dev]"
}
if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed" }
$receipt = Join-Path $Output "native"
$parameters = @("-I", (Join-Path $PSScriptRoot "acceptance.py"), "--wheel", $Wheel, "--seed", $Seed, "--output", $receipt, "--require-platform", "Windows")
if ($RequireMySql) { $parameters += "--require-mysql" }
& $interpreter @parameters
if ($LASTEXITCODE -ne 0) { throw "Native acceptance failed; preserve receipt.json" }
$xml = Join-Path $Output "tests.xml"
& $interpreter -I -m pytest (Join-Path $PSScriptRoot "../tests") -q "--junitxml=$xml"
if ($LASTEXITCODE -ne 0) { throw "Installed-wheel tests failed" }
if ($WithScheduler) {
  & $interpreter -I (Join-Path $PSScriptRoot "exercise_native_scheduler.py") --wheel $Wheel --output (Join-Path $Output "scheduler")
  if ($LASTEXITCODE -ne 0) { throw "Native scheduler exercise failed; preserve evidence" }
}
Write-Output "Native package checks passed. Complete scheduler evidence separately; keep private backups local."
