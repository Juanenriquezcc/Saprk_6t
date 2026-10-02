# Offline kit preparation: USED AT HOME, WITH INTERNET (run.cmd -PrepareKit).
# Downloads into the project folder:
#   tools\python  portable CPython 3.12: official python.org NuGet package (binaries
#                 signed by the PSF; Smart App Control blocks unsigned ones)
#   tools\jre     Eclipse Temurin JRE 17 (zip, no installer, signed)
#   wheels\       pyspark and py4j to install without Internet
# Installs nothing on the system and changes no environment variables.

$script:KitPythonMinor = '3.12'
$script:KitJavaFeature = 17
$script:NuGetPythonIndex = 'https://api.nuget.org/v3-flatcontainer/python/index.json'
$script:AdoptiumApi    = "https://api.adoptium.net/v3/assets/latest/$($script:KitJavaFeature)/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse"

function Save-Download([string]$Url, [string]$OutFile, [string]$Sha256) {
    Write-Host "  Descargando: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
    if ($Sha256) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutFile).Hash
        if ($actual -ne $Sha256.ToUpper()) {
            Remove-Item -LiteralPath $OutFile -Force
            throw "El checksum SHA256 no coincide para $(Split-Path -Leaf $OutFile)."
        }
        Write-Host '  Checksum SHA256 verificado.'
    } else {
        Write-Host '  Aviso: no hay checksum publicado; no se pudo verificar.' -ForegroundColor Yellow
    }
}

function Get-KitPython([string]$Root, [string]$Downloads) {
    $target = Join-Path $Root 'tools\python'
    if (Test-Path -LiteralPath (Join-Path $target 'python.exe')) { Write-Host 'Python portable: ya presente, se omite.'; return }

    $pattern = '^' + [regex]::Escape($script:KitPythonMinor) + '\.\d+$'
    $version = @((Invoke-RestMethod -Uri $script:NuGetPythonIndex -UseBasicParsing).versions |
                 Where-Object { $_ -match $pattern } | Sort-Object { [version]$_ })[-1]
    if (-not $version) { throw "No se encontro Python $($script:KitPythonMinor) en NuGet." }
    $leafUrl = (Invoke-RestMethod -Uri "https://api.nuget.org/v3/registration5-semver1/python/$version.json" -UseBasicParsing).catalogEntry
    $expectedSha512 = (Invoke-RestMethod -Uri $leafUrl -UseBasicParsing).packageHash   # base64

    if (-not (Confirm-Action "Descargar Python $version portable (paquete NuGet oficial de python.org, ~15 MB) a tools\python?")) {
        throw 'Descarga de Python cancelada.'
    }
    $archive = Join-Path $Downloads "python.$version.nupkg"
    Write-Host "  Descargando: https://api.nuget.org/v3-flatcontainer/python/$version/python.$version.nupkg"
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/python/$version/python.$version.nupkg" -OutFile $archive -UseBasicParsing
    $hex = (Get-FileHash -Algorithm SHA512 -LiteralPath $archive).Hash
    $actualSha512 = [Convert]::ToBase64String([byte[]]($hex -split '(..)' | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_, 16) }))
    if ($actualSha512 -ne $expectedSha512) { throw 'El checksum SHA512 del paquete de Python no coincide.' }
    Write-Host '  Checksum SHA512 verificado.'

    $extract = Join-Path $Downloads 'python_extract'
    New-Item -ItemType Directory -Force -Path $extract | Out-Null
    & tar.exe -xf $archive -C $extract   # the .nupkg is a zip; Python lives in tools\
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo extraer Python.' }
    $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $extract 'tools\python.exe')
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw "python.exe no tiene una firma valida de la Python Software Foundation ($($signature.Status))."
    }
    Write-Host '  Firma digital verificada (Python Software Foundation).'
    Move-Item -LiteralPath (Join-Path $extract 'tools') -Destination $target
    Write-Host '  Python portable listo en tools\python.' -ForegroundColor Green
}

function Get-KitJre([string]$Root, [string]$Downloads) {
    $target = Join-Path $Root 'tools\jre'
    if (Test-Path -LiteralPath (Join-Path $target 'bin\java.exe')) { Write-Host 'JRE: ya presente, se omite.'; return }

    $info = @(Invoke-RestMethod -Uri $script:AdoptiumApi -UseBasicParsing)[0]
    $pkg = $info.binary.package
    $sizeMb = [math]::Round($pkg.size / 1MB)
    if (-not (Confirm-Action "Descargar $($pkg.name) (Temurin $($info.version.openjdk_version), ~$sizeMb MB) a tools\jre?")) {
        throw 'Descarga del JRE cancelada.'
    }
    $archive = Join-Path $Downloads $pkg.name
    Save-Download $pkg.link $archive $pkg.checksum

    $extract = Join-Path $Downloads 'jre_extract'
    New-Item -ItemType Directory -Force -Path $extract | Out-Null
    & tar.exe -xf $archive -C $extract
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo extraer el JRE.' }
    $inner = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    Move-Item -LiteralPath $inner.FullName -Destination $target
    if (-not (Test-Path -LiteralPath (Join-Path $target 'bin\java.exe'))) { throw 'El JRE extraido no contiene bin\java.exe.' }
    Write-Host '  JRE listo en tools\jre.' -ForegroundColor Green
}

function Get-KitWheels([string]$Root) {
    $pinned = Get-PinnedPySpark $Root
    $wheels = Join-Path $Root 'wheels'
    $hasPy4j = Test-Path -Path (Join-Path $wheels 'py4j-*.whl')
    if ((Find-PySparkWheel $Root $pinned) -and $hasPy4j) { Write-Host 'Wheels: ya presentes, se omite.'; return }

    if (-not (Confirm-Action "Descargar y preparar wheels de pyspark==$pinned y py4j (~450 MB) en wheels\?")) {
        throw 'Descarga de wheels cancelada.'
    }
    $python = Join-Path $Root 'tools\python\python.exe'
    & $python -m pip wheel --disable-pip-version-check --progress-bar off -r (Join-Path $Root 'requirements.txt') -w $wheels
    if ($LASTEXITCODE -ne 0 -or -not (Find-PySparkWheel $Root $pinned)) { throw 'No se pudieron preparar los wheels.' }
    Write-Host '  Wheels listos en wheels\.' -ForegroundColor Green
}

function Invoke-PrepareKit([string]$Root) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'   # the PS 5.1 progress bar makes downloads very slow
    $downloads = Join-Path $Root 'tools\_downloads'
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    try {
        Write-Host ''; Write-Host '[1/3] Python portable'; Get-KitPython $Root $downloads
        Write-Host ''; Write-Host '[2/3] Java (JRE)';      Get-KitJre $Root $downloads
        Write-Host ''; Write-Host '[3/3] Wheels PySpark';  Get-KitWheels $Root
    } catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host 'El kit quedo incompleto. Puede volver a ejecutar run.cmd -PrepareKit (lo ya descargado se conserva).'
        return $false
    } finally {
        # Only the temporary files created by this script are removed.
        if (Test-Path -LiteralPath $downloads) { Remove-Item -LiteralPath $downloads -Recurse -Force }
    }
    Write-Host ''
    Write-Host 'Kit offline completo. Verifiquelo con: run.cmd -CheckOnly -KitOnly' -ForegroundColor Green
    return $true
}
