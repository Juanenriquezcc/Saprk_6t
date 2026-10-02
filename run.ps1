<#
.SYNOPSIS
  Orchestrator of PySpark Lab Analyzer (called from run.cmd).

.DESCRIPTION
  1. Checks the environment (Python, Java, JAVA_HOME, .venv, PySpark, Spark, resources, kit).
  2. If something is missing, offers a fix using ONLY the USB resources and asks first.
  3. Runs every check again and, when the environment is ready, starts main.py.
  It always ends with ENVIRONMENT READY or ENVIRONMENT NOT READY, and READY is only
  printed after Spark really started and ran SQL.

  It never downloads anything during the lab, never changes PATH or system variables
  (JAVA_HOME is set for this process only) and never changes PowerShell policies.

.EXAMPLE
  run.cmd                          Check, ask for the dataset and start
  run.cmd datos.csv --lab          Start with a dataset directly in lab mode
  run.cmd --check                  Diagnose only (same as -CheckOnly)
  run.cmd --KitOnly datos.csv --lab  Use only the USB Python/Java (clean PC, offline)
  run.cmd --rebuild-venv           Recreate the virtual environment from the USB
  run.cmd -PrepareKit              AT HOME, with Internet: download the offline kit
#>
param(
    [switch]$CheckOnly,
    [switch]$KitOnly,
    [switch]$AssumeYes,
    [switch]$AssumeNo,
    [switch]$PrepareKit,
    [switch]$RebuildVenv
)

# GNU-style aliases (--check, --KitOnly...) come in $args; everything else goes to main.py.
$aliases = @{ '--check' = 'CheckOnly'; '--checkonly' = 'CheckOnly'; '--kitonly' = 'KitOnly';
              '--preparekit' = 'PrepareKit'; '--rebuild-venv' = 'RebuildVenv'; '--rebuildvenv' = 'RebuildVenv';
              '--assume-yes' = 'AssumeYes'; '--assume-no' = 'AssumeNo' }
$appArgs = @()
foreach ($a in $args) {
    $flag = $aliases["$a".ToLower()]
    if ($flag) { Set-Variable -Name $flag -Value ([switch]$true) } else { $appArgs += $a }
}

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
. (Join-Path $Root 'bootstrap\checks.ps1')
. (Join-Path $Root 'bootstrap\install.ps1')

$Rule = '=' * 52

function Write-Banner([string]$Title, [string]$Color = 'White') {
    Write-Host $Rule
    Write-Host ' PYSPARK LAB ANALYZER'
    Write-Host " $Title" -ForegroundColor $Color
    Write-Host $Rule
}

function Stop-NotReady([string]$Message, [int]$Code = 1) {
    Write-Host ''
    if ($Message) { Write-Host $Message -ForegroundColor Red }
    Write-Banner 'ENVIRONMENT NOT READY' 'Red'
    Write-Host 'Ayuda rapida: EMERGENCY.md'
    exit $Code
}

function Confirm-Action([string]$Question) {
    if ($AssumeYes) { Write-Host "$Question [s/N] s (automatico)"; return $true }
    if ($AssumeNo)  { Write-Host "$Question [s/N] n (automatico)"; return $false }
    $answer = Read-Host "$Question [s/N]"
    return ($answer -match '^\s*(s|si|y|yes)\s*$')
}

if ($PrepareKit) {
    . (Join-Path $Root 'bootstrap\prepare_kit.ps1')
    Write-Banner 'PREPARACION DEL KIT OFFLINE (requiere Internet)'
    if (Invoke-PrepareKit -Root $Root) { exit 0 } else { exit 1 }
}

Write-Banner 'ENVIRONMENT CHECK'
Write-Host "Proyecto: $Root"

$arch = Get-OsArchitecture
if ($arch -ne 'AMD64') {
    Write-Host ''
    Write-Host "Arquitectura $arch no soportada por el kit." -ForegroundColor Red
    Write-Host '  Por que : el kit de la USB incluye Python y Java solo para Windows x64.'
    Write-Host "  Como    : en un equipo $arch instale manualmente Python 3.12 y Java 17 para $arch,"
    Write-Host '            y cree el entorno desde la USB (los wheels sirven en cualquier arquitectura):'
    Write-Host '            python -m venv .venv  y  .venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt'
    Stop-NotReady '' 2
}

Clear-ForeignEnvironment

if ($RebuildVenv) {
    $ctx = New-CheckContext -Root $Root -KitOnly $KitOnly.IsPresent
    [void](Test-Python $ctx)
    if (-not $ctx.Python) { Stop-NotReady 'No se puede recrear el entorno virtual: falta Python (ver tools\python).' }
    [void](Test-Venv $ctx)
    if ($ctx.VenvDir) { $ctx.VenvTarget = $ctx.VenvDir }
    if (-not $ctx.VenvTarget) { $ctx.VenvTarget = (Get-VenvDirs $Root)[0] }
    if (-not (Confirm-Action "Recrear el entorno virtual en $($ctx.VenvTarget) desde la USB (sin Internet)?")) {
        Stop-NotReady 'Cancelado.'
    }
    try { Install-Venv $ctx } catch { Stop-NotReady "La solucion fallo: $($_.Exception.Message)" }
}

$maxRounds = 3
for ($round = 1; $round -le $maxRounds; $round++) {
    $ctx = New-CheckContext -Root $Root -KitOnly $KitOnly.IsPresent
    $checks = Invoke-AllChecks $ctx
    $failed = @($checks | Where-Object { $_.Status -eq 'ERROR' })
    if ($failed.Count -eq 0) { break }

    foreach ($c in $failed) { Write-Problem $c }
    $manual = @($failed | Where-Object { -not $_.FixId })
    if ($manual.Count -gt 0) {
        Stop-NotReady ("No se puede continuar: hay componentes que no pueden instalarse automaticamente (ver arriba).`n" +
                       'No se realizo ningun cambio.')
    }
    if ($round -eq $maxRounds) { break }

    foreach ($c in $failed) {
        Write-Host ''
        if (-not (Confirm-Action "Aplicar la solucion para '$($c.Name)'?")) {
            Stop-NotReady 'Cancelado. No se realizaron mas cambios.'
        }
        try { Invoke-Fix $ctx $c }
        catch { Stop-NotReady "La solucion fallo: $($_.Exception.Message)" }
    }
    Write-Host ''
    Write-Host 'Volviendo a ejecutar todas las comprobaciones...'
}

if ($failed.Count -gt 0) { Stop-NotReady 'El entorno sigue con errores despues de las correcciones.' }

Write-Host ''
Write-Banner 'ENVIRONMENT READY' 'Green'
if ($CheckOnly) { exit 0 }

Write-Host 'Starting laboratory...'
Write-Host ''
& $ctx.VenvPython (Join-Path $Root 'main.py') @appArgs
exit $LASTEXITCODE
