# Environment checks.
# No function changes the system. The only changes are environment variables of
# THIS PROCESS (JAVA_HOME, PATH, PYSPARK_PYTHON), which disappear when it ends.

$script:MinPython      = [version]'3.10'
$script:SupportedJava  = @(17, 21, 25)
$script:LabCheckMarker = '##LABCHECK##'
$script:TotalChecks    = 9

# --- Helpers ---------------------------------------------------------------

function New-Check {
    param([string]$Name, [string]$Status, [string]$Detail,
          [string]$Why = '', [string]$Fix = '', [string]$FixId = '', [string[]]$Commands = @())
    [pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail; Why = $Why
                       Fix = $Fix; FixId = $FixId; Commands = $Commands }
}

function ConvertTo-ArgString([string[]]$Arguments) {
    ($Arguments | ForEach-Object {
        if ($_ -eq '') { '""' }
        elseif ($_ -match '[\s"]') { '"' + (($_ -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"' }
        else { $_ }
    }) -join ' '
}

# Runs a program capturing stdout/stderr without mixing them with PowerShell errors.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments = @(), [int]$TimeoutSec = 120)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ConvertTo-ArgString $Arguments
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    try { $p = [System.Diagnostics.Process]::Start($psi) }
    catch { return [pscustomobject]@{ ExitCode = -1; StdOut = ''; StdErr = $_.Exception.Message } }
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch { }
        return [pscustomobject]@{ ExitCode = -1; StdOut = ''; StdErr = "Tiempo de espera agotado ($TimeoutSec s)." }
    }
    $p.WaitForExit()
    [pscustomobject]@{ ExitCode = $p.ExitCode; StdOut = $out.Result; StdErr = $err.Result }
}

function Test-Writable([string]$Dir) {
    if (-not $Dir -or -not (Test-Path -LiteralPath $Dir)) { return $false }
    $probe = Join-Path $Dir (".write_test_" + [guid]::NewGuid().ToString('N'))
    try { [IO.File]::WriteAllText($probe, 'x'); Remove-Item -LiteralPath $probe -Force; return $true }
    catch { return $false }
}

function Get-DisplayPath($Ctx, [string]$Path) {
    if ($Path -and $Path.StartsWith($Ctx.Root, [StringComparison]::OrdinalIgnoreCase)) {
        return '.' + $Path.Substring($Ctx.Root.TrimEnd('\').Length)
    }
    return $Path
}

function Get-OsArchitecture {
    if ($env:PROCESSOR_ARCHITEW6432) { return $env:PROCESSOR_ARCHITEW6432 }
    return $env:PROCESSOR_ARCHITECTURE
}

# Variables that could make ANOTHER Spark or Python be used: removed for this process only.
function Clear-ForeignEnvironment {
    $script:OriginalJavaHome = $env:JAVA_HOME
    foreach ($name in 'SPARK_HOME', 'PYSPARK_SUBMIT_ARGS', 'PYTHONHOME', 'PYTHONPATH',
                      'PYSPARK_PYTHON', 'PYSPARK_DRIVER_PYTHON', 'VIRTUAL_ENV') {
        if (Test-Path "Env:$name") { Remove-Item "Env:$name" }
    }
    $env:SPARK_LOCAL_IP = '127.0.0.1'
}

function New-CheckContext([string]$Root, [bool]$KitOnly) {
    @{ Root = $Root; KitOnly = $KitOnly; Python = $null; Java = $null
       VenvDir = $null; VenvPython = $null; VenvTarget = $null; PySparkOk = $false }
}

function Get-PinnedPySpark([string]$Root) {
    $m = Select-String -LiteralPath (Join-Path $Root 'requirements.txt') -Pattern '^pyspark==(\S+)' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Find-PySparkWheel([string]$Root, [string]$Version) {
    $dir = Join-Path $Root 'wheels'
    if (-not (Test-Path -LiteralPath $dir)) { return $null }
    Get-ChildItem -LiteralPath $dir -Filter "pyspark-$Version-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
}

# --- 1. Python ----------------------------------------------------------------

function Test-IsStoreAlias([string]$Path) {
    # The Microsoft Store "python.exe" is not Python: it opens the store.
    return [bool]($Path -match '\\Microsoft\\WindowsApps\\')
}

function Get-PythonInfo([string]$Exe) {
    if (-not $Exe -or (Test-IsStoreAlias $Exe) -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    $r = Invoke-Native $Exe @('-c', 'import sys, platform; print(platform.python_version()); print(sys.executable)')
    if ($r.ExitCode -ne 0) { return $null }
    $lines = @($r.StdOut -split "`r?`n" | Where-Object { $_ })
    if ($lines.Count -lt 2 -or $lines[0] -notmatch '^(\d+\.\d+\.\d+)') { return $null }
    [pscustomobject]@{ Exe = $lines[1].Trim(); Version = [version]$Matches[1] }
}

function Get-RunFailureReason([string]$Exe) {
    $r = Invoke-Native $Exe @('-c', 'pass') 60
    if ($r.ExitCode -eq -1058471934) {   # 0xC0E90002
        return "$Exe bloqueado por Smart App Control de Windows (ejecutable sin firma digital)"
    }
    return "$Exe no se pudo ejecutar (codigo $($r.ExitCode))"
}

function Find-Python([string]$Root, [bool]$KitOnly) {
    $rejected = @()
    # The portable Python on the USB comes first.
    $kitExe = Join-Path $Root 'tools\python\python.exe'
    $kit = Get-PythonInfo $kitExe
    if ($kit -and $kit.Version -ge $script:MinPython) { return @{ Found = $kit; Source = 'kit USB'; Rejected = $rejected } }
    if (-not $kit -and (Test-Path -LiteralPath $kitExe)) { $rejected += Get-RunFailureReason $kitExe }
    if ($KitOnly) { return @{ Found = $null; Rejected = $rejected } }

    $candidates = @()
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in '3.12', '3.13', '3.11', '3.10') {
            $r = Invoke-Native $py.Source @("-$v", '-c', 'import sys; print(sys.executable)')
            if ($r.ExitCode -eq 0) { $candidates += $r.StdOut.Trim() }
        }
    }
    $candidates += @(Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($c in ($candidates | Select-Object -Unique)) {
        $info = Get-PythonInfo $c
        if (-not $info) { continue }
        if ($info.Version -ge $script:MinPython) { return @{ Found = $info; Source = 'sistema'; Rejected = $rejected } }
        $rejected += "Python $($info.Version) en $($info.Exe)"
    }
    return @{ Found = $null; Rejected = $rejected }
}

function Test-Python($Ctx) {
    $r = Find-Python $Ctx.Root $Ctx.KitOnly
    if ($r.Found) {
        $Ctx.Python = $r.Found.Exe
        return New-Check 'Python' 'OK' "$($r.Found.Version) ($($r.Source))"
    }
    $detail = "No se encontro Python $script:MinPython o superior (se busco tools\python\python.exe"
    $detail += $(if ($Ctx.KitOnly) { ')' } else { ', py.exe y python.exe del sistema)' })
    if ($r.Rejected) { $detail += '. Descartados: ' + ($r.Rejected -join '; ') }
    New-Check 'Python' 'ERROR' $detail `
        -Why 'La aplicacion y PySpark se ejecutan con Python; tambien se necesita para crear el entorno virtual.' `
        -Fix 'Copie el kit completo a la USB (carpeta tools\python). Para generarlo, en un equipo con Internet: run.cmd -PrepareKit'
}

# --- 2. Java ------------------------------------------------------------------

function Get-JavaInfo([string]$JavaExe) {
    if (-not $JavaExe -or -not (Test-Path -LiteralPath $JavaExe)) { return $null }
    $r = Invoke-Native $JavaExe @('-XshowSettings:properties', '-version') 60
    $text = "$($r.StdErr)`n$($r.StdOut)"
    if ($text -notmatch 'java\.version = (\S+)') { return $null }
    $version = $Matches[1]
    $parts = $version.Split('.')
    $major = [int]($parts[0] -replace '\D.*$', '')
    if ($major -eq 1 -and $parts.Count -gt 1) { $major = [int]$parts[1] }   # 1.8 -> 8
    $javaHome = if ($text -match 'java\.home = (.+)') { $Matches[1].Trim() } else { Split-Path (Split-Path $JavaExe) }
    [pscustomobject]@{ Exe = $JavaExe; Version = $version; Major = $major; Home = $javaHome }
}

function Find-Java([string]$Root, [bool]$KitOnly) {
    $paths = @(Join-Path $Root 'tools\jre\bin\java.exe')   # the USB JRE comes first
    if (-not $KitOnly) {
        if ($env:JAVA_HOME) { $paths += Join-Path $env:JAVA_HOME 'bin\java.exe' }
        $paths += @(Get-Command java.exe -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
        foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }) {
            foreach ($vendor in 'Eclipse Adoptium', 'Java', 'Microsoft', 'Zulu', 'Amazon Corretto', 'BellSoft') {
                $paths += @(Get-ChildItem -Path (Join-Path $base "$vendor\*\bin\java.exe") -ErrorAction SilentlyContinue |
                            ForEach-Object { $_.FullName })
            }
        }
    }
    $rejected = @()
    foreach ($p in ($paths | Select-Object -Unique)) {
        $info = Get-JavaInfo $p
        if (-not $info) { continue }
        if ($script:SupportedJava -contains $info.Major) { return @{ Found = $info; Rejected = $rejected } }
        $rejected += "Java $($info.Version) en $p"
    }
    return @{ Found = $null; Rejected = $rejected }
}

function Test-Java($Ctx) {
    $r = Find-Java $Ctx.Root $Ctx.KitOnly
    if ($r.Found) {
        $Ctx.Java = $r.Found
        $source = if ($r.Found.Exe.StartsWith($Ctx.Root, [StringComparison]::OrdinalIgnoreCase)) { 'kit USB' } else { 'sistema' }
        return New-Check 'Java' 'OK' "Java $($r.Found.Version) ($source)"
    }
    $versions = $script:SupportedJava -join '/'
    $detail = "No se encontro Java $versions"
    if ($r.Rejected) { $detail += '. Incompatibles: ' + ($r.Rejected -join '; ') }
    New-Check 'Java' 'ERROR' $detail `
        -Why "Spark se ejecuta sobre la maquina virtual de Java: sin Java $versions no puede iniciar." `
        -Fix 'Copie el kit completo a la USB (carpeta tools\jre). Para generarlo, en un equipo con Internet: run.cmd -PrepareKit'
}

# --- 3. JAVA_HOME (this process only) ------------------------------------

function Test-JavaHome($Ctx) {
    if (-not $Ctx.Java) { return New-Check 'JAVA_HOME' 'SKIP' 'requiere Java' }
    $javaHome = $Ctx.Java.Home
    $previous = $script:OriginalJavaHome   # the value run.cmd started with
    $env:JAVA_HOME = $javaHome
    $javaBin = Join-Path $javaHome 'bin'
    if (-not $env:PATH.StartsWith("$javaBin;", [StringComparison]::OrdinalIgnoreCase)) { $env:PATH = "$javaBin;$env:PATH" }
    $shown = Get-DisplayPath $Ctx $javaHome
    if ($previous -and ($previous.TrimEnd('\') -eq $javaHome.TrimEnd('\'))) { return New-Check 'JAVA_HOME' 'OK' $shown }
    New-Check 'JAVA_HOME' 'OK' "$shown (definido solo para este proceso)"
}

# --- 4. Virtual environment -------------------------------------------------------

function Get-VenvDirs([string]$Root) {
    $dirs = @(Join-Path $Root '.venv')
    if ($env:LOCALAPPDATA) { $dirs += Join-Path $env:LOCALAPPDATA 'PySparkLabAnalyzer\.venv' }
    $dirs
}

function Get-VenvState([string]$Dir) {
    $py = Join-Path $Dir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) { return 'missing' }
    $r = Invoke-Native $py @('-c', 'import sys; print(sys.prefix)') 60
    if ($r.ExitCode -ne 0) { return 'broken' }
    return 'ok'
}

function Test-Venv($Ctx) {
    $dirs = @(Get-VenvDirs $Ctx.Root)
    $broken = $null
    foreach ($d in $dirs) {
        $state = Get-VenvState $d
        if ($state -eq 'ok') {
            $Ctx.VenvDir = $d
            $Ctx.VenvPython = Join-Path $d 'Scripts\python.exe'
            return New-Check 'Virtual environment' 'OK' (Get-DisplayPath $Ctx $d)
        }
        if ($state -eq 'broken' -and -not $broken) { $broken = $d }
    }
    if (-not $Ctx.Python) { return New-Check 'Virtual environment' 'SKIP' 'requiere Python' }

    if ($broken) {
        $target = $broken
        $detail = "$(Get-DisplayPath $Ctx $broken) existe pero no funciona (creado en otro equipo o en otra ubicacion/letra de unidad)"
    } elseif (Test-Writable $Ctx.Root) {
        $target = $dirs[0]
        $detail = 'no existe .venv'
    } elseif ($dirs.Count -gt 1) {
        $target = $dirs[1]
        $detail = "no existe .venv y la carpeta del proyecto no permite escritura; se usara $target"
    } else {
        return New-Check 'Virtual environment' 'ERROR' 'La carpeta del proyecto no permite escritura y no hay LOCALAPPDATA.' `
            -Why 'El entorno virtual necesita una carpeta con permiso de escritura.' `
            -Fix 'Copie el proyecto a una carpeta con permiso de escritura (por ejemplo, el Escritorio) y ejecute run.cmd alli.'
    }
    $Ctx.VenvTarget = $target
    $venvPy = Join-Path $target 'Scripts\python.exe'
    $commands = @("`"$($Ctx.Python)`" -m venv --clear `"$target`"")
    $pinned = Get-PinnedPySpark $Ctx.Root
    if (Find-PySparkWheel $Ctx.Root $pinned) {
        $commands += "`"$venvPy`" -m pip install --no-index --find-links `"$(Join-Path $Ctx.Root 'wheels')`" -r requirements.txt"
    }
    New-Check 'Virtual environment' 'ERROR' $detail -FixId 'venv' -Commands $commands `
        -Why 'Aisla PySpark y sus dependencias sin modificar el Python del equipo.'
}

# --- 5. PySpark ---------------------------------------------------------------

function Test-PySpark($Ctx) {
    if (-not $Ctx.VenvPython) { return New-Check 'PySpark' 'SKIP' 'requiere el entorno virtual' }
    $pinned = Get-PinnedPySpark $Ctx.Root
    $r = Invoke-Native $Ctx.VenvPython @('-c', 'import pyspark; print(pyspark.__version__)') 120
    $installed = if ($r.ExitCode -eq 0) { $r.StdOut.Trim() } else { $null }
    if ($installed -and (-not $pinned -or $installed -eq $pinned)) {
        $Ctx.PySparkOk = $true
        return New-Check 'PySpark' 'OK' $installed
    }
    $detail = if ($installed) { "version $installed instalada; se requiere $pinned" } else { 'no esta instalado en el entorno virtual' }
    $why = 'Es el motor de procesamiento de la aplicacion (PySpark + Spark SQL).'
    if (Find-PySparkWheel $Ctx.Root $pinned) {
        return New-Check 'PySpark' 'ERROR' $detail -Why $why -FixId 'pyspark' `
            -Commands @("`"$($Ctx.VenvPython)`" -m pip install --no-index --find-links `"$(Join-Path $Ctx.Root 'wheels')`" -r requirements.txt")
    }
    New-Check 'PySpark' 'ERROR' "$detail; no hay wheels\pyspark-$pinned-*.whl en la USB" -Why $why `
        -Fix 'Copie el kit completo (carpeta wheels). Para generarlo, en un equipo con Internet: run.cmd -PrepareKit'
}

# --- 6 and 7. SparkSession and Spark SQL (run by Python: main.py --check) -----

function Test-Spark($Ctx) {
    if (-not $Ctx.PySparkOk) {
        return @((New-Check 'SparkSession' 'SKIP' 'requiere PySpark'), (New-Check 'Spark SQL' 'SKIP' 'requiere PySpark'))
    }
    $env:PYSPARK_PYTHON = $Ctx.VenvPython
    $env:PYSPARK_DRIVER_PYTHON = $Ctx.VenvPython
    $r = Invoke-Native $Ctx.VenvPython @((Join-Path $Ctx.Root 'main.py'), '--check', '--json') 300
    $line = @($r.StdOut -split "`r?`n" | Where-Object { $_.StartsWith($script:LabCheckMarker) }) | Select-Object -Last 1
    $why = 'Spark debe arrancar localmente para cargar y consultar el dataset.'
    if (-not $line) {
        $tail = (("$($r.StdErr)`n$($r.StdOut)").Trim() -split "`r?`n" | Select-Object -Last 3) -join ' | '
        return @((New-Check 'SparkSession' 'ERROR' "main.py --check no devolvio resultados: $tail" -Why $why `
                    -Fix 'Ejecute run.cmd --rebuild-venv para recrear el entorno desde la USB. Detalle: .venv\Scripts\python.exe main.py --check --debug'),
                 (New-Check 'Spark SQL' 'SKIP' 'requiere SparkSession'))
    }
    $items = $line.Substring($script:LabCheckMarker.Length) | ConvertFrom-Json
    foreach ($name in 'SparkSession', 'Spark SQL') {
        $it = $items | Where-Object { $_.name -eq $name } | Select-Object -First 1
        $fix = if ($it.status -eq 'ERROR') { "$($it.hint) Si persiste: run.cmd --rebuild-venv".Trim() } else { $it.hint }
        New-Check $name $it.status $it.detail -Why $why -Fix $fix
    }
}

# --- 8. Resources --------------------------------------------------------------

function Test-Resources($Ctx) {
    $parts = @(); $status = 'OK'; $fix = ''
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $free = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
        $total = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
        $parts += "RAM libre $free/$total GB"
        if ($free -lt 2) { $status = 'WARN'; $fix = 'Poca RAM libre: cierre otras aplicaciones (Spark usa hasta 2 GB).' }
    } catch { $parts += 'RAM: no se pudo consultar'; $status = 'WARN' }
    $parts += "$([Environment]::ProcessorCount) nucleos"

    $dir = if ($Ctx.VenvDir) { $Ctx.VenvDir } elseif ($Ctx.VenvTarget) { $Ctx.VenvTarget } else { $Ctx.Root }
    try {
        $drive = New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot($dir))
        $freeDisk = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        $parts += "disco libre $freeDisk GB ($($drive.Name))"
        if (-not $Ctx.VenvDir -and $freeDisk -lt 1.5) { $status = 'WARN'; $fix = 'Crear el entorno virtual requiere ~1.5 GB libres.' }
    } catch { }

    if (-not (Test-Writable $env:TEMP)) {
        $status = 'ERROR'; $fix = "La carpeta temporal ($env:TEMP) no permite escritura; Spark la necesita."
    }
    New-Check 'Resources' $status ($parts -join ', ') -Fix $fix `
        -Why 'Spark necesita memoria y una carpeta temporal con escritura.'
}

# --- 9. Offline kit -------------------------------------------------------------

function Test-Kit($Ctx) {
    $pinned = Get-PinnedPySpark $Ctx.Root
    $parts = @(
        @('tools\python', (Test-Path -LiteralPath (Join-Path $Ctx.Root 'tools\python\python.exe'))),
        @('tools\jre', (Test-Path -LiteralPath (Join-Path $Ctx.Root 'tools\jre\bin\java.exe'))),
        @("wheels (pyspark $pinned)", [bool](Find-PySparkWheel $Ctx.Root $pinned))
    )
    $missing = @($parts | Where-Object { -not $_[1] } | ForEach-Object { $_[0] })
    if (-not $missing) { return New-Check 'Offline kit' 'OK' 'tools\python, tools\jre y wheels presentes' }
    # Not an error by itself: a PC with Python/Java/PySpark already works. Without the kit,
    # another PC without them could not be prepared offline.
    New-Check 'Offline kit' 'WARN' ('faltan: ' + ($missing -join ', ')) `
        -Fix 'Sin el kit completo la USB no sirve en un PC sin Python/Java. Preparelo en casa: run.cmd -PrepareKit'
}

# --- Running and printing -------------------------------------------------

function Write-CheckLine([int]$Index, $Check) {
    $label = ("[{0}/{1}] {2} " -f $Index, $script:TotalChecks, $Check.Name).PadRight(34, '.')
    $color = switch ($Check.Status) { 'OK' { 'Green' } 'WARN' { 'Yellow' } 'ERROR' { 'Red' } default { 'DarkGray' } }
    Write-Host "$label " -NoNewline
    Write-Host $Check.Status.PadRight(6) -ForegroundColor $color -NoNewline
    Write-Host "  $($Check.Detail)"
    if ($Check.Status -eq 'WARN' -and $Check.Fix) { Write-Host "        -> $($Check.Fix)" -ForegroundColor Yellow }
}

function Invoke-AllChecks($Ctx) {
    $results = @()
    $i = 0
    foreach ($test in 'Test-Python', 'Test-Java', 'Test-JavaHome', 'Test-Venv', 'Test-PySpark') {
        $c = & $test $Ctx; $i++; Write-CheckLine $i $c; $results += $c
    }
    if ($Ctx.PySparkOk) { Write-Host '        (iniciando Spark para comprobarlo; tarda unos segundos)' -ForegroundColor DarkGray }
    foreach ($c in (Test-Spark $Ctx)) { $i++; Write-CheckLine $i $c; $results += $c }
    $c = Test-Resources $Ctx; $i++; Write-CheckLine $i $c; $results += $c
    $c = Test-Kit $Ctx; $i++; Write-CheckLine $i $c; $results += $c
    return $results
}

function Write-Problem($Check) {
    Write-Host ''
    Write-Host "ERROR: $($Check.Name)" -ForegroundColor Red
    Write-Host "  Que falta : $($Check.Detail)"
    if ($Check.Why) { Write-Host "  Por que   : $($Check.Why)" }
    if ($Check.FixId) {
        Write-Host '  Solucion  : automatica, solo con recursos de la USB (sin Internet)'
        Write-Host '  Comandos  :'
        foreach ($cmd in $Check.Commands) { Write-Host "    $cmd" }
    } else {
        Write-Host '  Solucion  : manual (no puede instalarse automaticamente)'
        if ($Check.Fix) { Write-Host "  Como      : $($Check.Fix)" }
    }
}
