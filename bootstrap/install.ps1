# Automatic fixes. They only use USB resources (tools\python and wheels\) and
# only write inside the virtual environment folder. They never download anything.

function Invoke-Fix($Ctx, $Check) {
    switch ($Check.FixId) {
        'venv'    { Install-Venv $Ctx }
        'pyspark' { Install-PySparkFromWheels $Ctx $Ctx.VenvPython }
        default   { throw "No hay solucion automatica para '$($Check.Name)'." }
    }
}

function Install-Venv($Ctx) {
    $target = $Ctx.VenvTarget
    # Safety: only a folder named .venv that is empty or already a virtual environment is (re)created.
    if ((Split-Path -Leaf $target) -ne '.venv') { throw "Destino inesperado para el entorno virtual: $target" }
    if ((Test-Path -LiteralPath $target) -and -not (Test-Path -LiteralPath (Join-Path $target 'pyvenv.cfg'))) {
        throw "$target existe y no es un entorno virtual; no se modificara."
    }
    $parent = Split-Path $target
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }   # e.g. %LOCALAPPDATA%\PySparkLabAnalyzer

    Write-Host "Creando entorno virtual en $target ..."
    & $Ctx.Python -m venv --clear $target
    if ($LASTEXITCODE -ne 0) { throw "python -m venv termino con codigo $LASTEXITCODE." }

    $venvPy = Join-Path $target 'Scripts\python.exe'
    if (Find-PySparkWheel $Ctx.Root (Get-PinnedPySpark $Ctx.Root)) { Install-PySparkFromWheels $Ctx $venvPy }
}

function Install-PySparkFromWheels($Ctx, [string]$VenvPython) {
    Write-Host 'Instalando PySpark desde wheels\ (sin Internet; puede tardar 1-3 minutos en una USB)...'
    & $VenvPython -m pip install --no-index --find-links (Join-Path $Ctx.Root 'wheels') `
        -r (Join-Path $Ctx.Root 'requirements.txt') --disable-pip-version-check --progress-bar off
    if ($LASTEXITCODE -ne 0) { throw "pip install termino con codigo $LASTEXITCODE." }
}
