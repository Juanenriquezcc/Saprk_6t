"""Pruebas del bootstrap portátil (run.cmd / run.ps1).

Requieren el kit offline (run.cmd -PrepareKit). Todas se ejecutan SIN Internet:
pip no puede usar índices y cualquier proxy HTTP apunta a un puerto cerrado.
"""
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT_PYTHON = ROOT / "tools" / "python"
KIT_JRE = ROOT / "tools" / "jre"
KIT_READY = ((KIT_PYTHON / "python.exe").exists() and (KIT_JRE / "bin" / "java.exe").exists()
             and any((ROOT / "wheels").glob("pyspark-*.whl")))
pytestmark = pytest.mark.skipif(not KIT_READY, reason="kit offline no preparado (run.cmd -PrepareKit)")

OFFLINE = {"PIP_NO_INDEX": "1", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
# Variables que el bootstrap debe resolver por sí mismo.
CLEARED = ("JAVA_HOME", "PYSPARK_PYTHON", "PYSPARK_DRIVER_PYTHON", "VIRTUAL_ENV", "SPARK_HOME")


def run(entry, *args, env=None, timeout=900):
    e = {k: v for k, v in os.environ.items() if k.upper() not in CLEARED}
    e.update(OFFLINE)
    e.update(env or {})
    entry = Path(entry)
    if entry.suffix == ".cmd":
        cmd = ["cmd.exe", "/c", str(entry), *args]
    else:
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(entry), *args]
    p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, env=e, timeout=timeout)
    raw = p.stdout + p.stderr
    try:
        return p.returncode, raw.decode("utf-8")
    except UnicodeDecodeError:
        return p.returncode, raw.decode("oem", errors="replace")


def check_status(out, number):
    """Estado del check en la ÚLTIMA ronda de comprobaciones."""
    found = re.findall(rf"\[{number}/\d+\] .*?\.+ (OK|WARN|ERROR|SKIP)", out)
    return found[-1] if found else None


def assert_ready(rc, out):
    assert rc == 0, out
    assert "ENVIRONMENT READY" in out
    assert [check_status(out, i) for i in range(1, 8)] == ["OK"] * 7, out
    assert check_status(out, 8) in ("OK", "WARN"), out  # recursos: WARN si hay poca RAM libre
    assert check_status(out, 9) == "OK", out            # kit offline completo


def test_ready_offline_via_run_cmd():
    rc, out = run(ROOT / "run.cmd", "-CheckOnly", "-AssumeNo", "-KitOnly")
    assert_ready(rc, out)
    assert "(kit USB)" in out
    assert "definido solo para este proceso" in out or "JAVA_HOME" in out


def test_arm_is_reported_as_unsupported():
    rc, out = run(ROOT / "run.ps1", "-CheckOnly", env={"PROCESSOR_ARCHITECTURE": "ARM64"})
    assert rc == 2, out
    assert "ARM64" in out and "no soportada" in out


def test_store_alias_is_ignored():
    checks = ROOT / "bootstrap" / "checks.ps1"
    script = (f". '{checks}'; "
              r"$a = Test-IsStoreAlias 'C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe'; "
              r"$b = Test-IsStoreAlias 'C:\Python312\python.exe'; "
              r"$c = Get-PythonInfo 'C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe'; "
              "if ($a -and -not $b -and $null -eq $c) { exit 0 } else { exit 1 }")
    p = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr


# --- Escenario USB: copia del proyecto en otra letra de unidad ----------------

def snapshot(folder):
    """Lista de archivos (sin entrar en los enlaces al kit) para detectar cambios."""
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not os.path.isjunction(os.path.join(dirpath, d))]
        files += [os.path.relpath(os.path.join(dirpath, f), folder) for f in filenames]
    return sorted(files)


def free_letter(exclude=()):
    used = {d[0].upper() for d in os.listdrives()}
    for letter in "ZYXWVUTSRQPONM":
        if letter not in used and letter not in exclude:
            return letter
    pytest.skip("no hay letras de unidad libres para simular la USB")


@contextmanager
def subst(folder, exclude=()):
    letter = free_letter(exclude)
    subprocess.run(["subst", f"{letter}:", str(folder)], check=True, capture_output=True)
    try:
        yield Path(f"{letter}:\\")
    finally:
        subprocess.run(["subst", f"{letter}:", "/D"], capture_output=True)


@pytest.fixture
def usb_copy(tmp_path):
    """Copia ligera del proyecto; el kit (pesado) se enlaza con junctions, sin copiarlo."""
    copy = tmp_path / "usb" / "pyspark_lab_analyzer"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
        ".venv", "tools", "wheels", "__pycache__", ".pytest_cache"))
    (copy / "tools").mkdir()
    links = []

    def link(name, target):
        path = copy / name
        subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)], check=True, capture_output=True)
        links.append(path)

    yield copy, link
    for path in links:
        os.rmdir(path)  # elimina SOLO el enlace, nunca el kit original
    shutil.rmtree(copy, ignore_errors=True)


@pytest.mark.slow
def test_portable_usb_scenario(usb_copy):
    copy, link = usb_copy
    link(r"tools\python", KIT_PYTHON)
    link("wheels", ROOT / "wheels")

    # 1) Sin JRE ni .venv: error claro de Java y ningún cambio en disco.
    before = snapshot(copy)
    rc, out = run(copy / "run.ps1", "-CheckOnly", "-AssumeNo", "-KitOnly")
    assert rc == 1, out
    assert check_status(out, 2) == "ERROR" and "Por que" in out and "-PrepareKit" in out
    assert "No se realizo ningun cambio" in out
    assert snapshot(copy) == before

    # 2) Con JRE: falta el .venv; se ofrece crearlo (sin Internet) y al rechazarlo no cambia nada.
    link(r"tools\jre", KIT_JRE)
    before = snapshot(copy)
    rc, out = run(copy / "run.ps1", "-CheckOnly", "-AssumeNo", "-KitOnly")
    assert rc == 1, out
    assert check_status(out, 4) == "ERROR" and "-m venv" in out and "--no-index" in out
    assert snapshot(copy) == before

    # 3) "USB" en otra letra, aceptando: crea .venv desde wheels\ sin Internet y queda listo.
    with subst(copy) as drive:
        first_letter = drive.drive[0]
        rc, out = run(drive / "run.cmd", "-CheckOnly", "-AssumeYes", "-KitOnly")
        assert_ready(rc, out)
        assert f"Proyecto: {drive}" in out
    assert (copy / ".venv" / "Scripts" / "python.exe").exists()

    # 4) La USB cambia de letra: el .venv apunta a la anterior; se detecta y se recrea offline.
    with subst(copy, exclude=(first_letter,)) as drive:
        rc, out = run(drive / "run.cmd", "-CheckOnly", "-AssumeNo", "-KitOnly")
        assert rc == 1, out
        assert check_status(out, 4) == "ERROR" and "otro equipo" in out
        rc, out = run(drive / "run.cmd", "-CheckOnly", "-AssumeYes", "-KitOnly")
        assert_ready(rc, out)
