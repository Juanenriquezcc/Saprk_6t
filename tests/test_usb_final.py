"""Final offline USB rehearsal (slow).

The project is copied with the SAME robocopy command documented in README.md (no .venv),
then run.cmd is used from three different drive letters (subst) with:
  - no Internet (pip without index, HTTP(S) proxy to a closed port),
  - PATH reduced to Windows system folders (no Python / Java of this PC on PATH),
  - no JAVA_HOME and an empty LOCALAPPDATA,
  - --KitOnly where a clean PC is simulated.
"""
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT_READY = ((ROOT / "tools" / "python" / "python.exe").exists() and (ROOT / "tools" / "jre" / "bin" / "java.exe").exists()
             and any((ROOT / "wheels").glob("pyspark-*.whl")))
pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not KIT_READY, reason="kit offline no preparado (run.cmd -PrepareKit)")]

# Keep in sync with README.md ("Preparar la USB"). __pycache__ is kept on purpose: the
# portable Python's precompiled standard library makes the first start faster.
USB_EXCLUDE_DIRS = [".venv", "exports", ".pytest_cache"]
DATASET = r"tests\data\stocks_small.csv"
APP_INPUT = ["",                                                        # CSV options OK
             "¿Cuál empresa presentó el mayor volumen total negociado?", "",
             ":q",                                                      # leave lab mode
             "8", "SELECT COUNT(*) AS n FROM dataset;", ":q",           # Spark SQL
             "0"]


def copy_to_usb(target):
    cmd = ["robocopy", str(ROOT), str(target), "/E", "/XD", *USB_EXCLUDE_DIRS, "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
    rc = subprocess.run(cmd, capture_output=True).returncode
    assert rc < 8, f"robocopy fallo ({rc})"     # 0-7 = success


def clean_env(tmp):
    system = os.environ["SystemRoot"]
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PATH", "JAVA_HOME", "PYSPARK_PYTHON", "PYSPARK_DRIVER_PYTHON", "VIRTUAL_ENV",
                                "SPARK_HOME", "PYTHONPATH", "PYTHONHOME", "LOCALAPPDATA")}
    env["PATH"] = ";".join([rf"{system}\System32", system, rf"{system}\System32\Wbem",
                            rf"{system}\System32\WindowsPowerShell\v1.0"])
    env.update({"PIP_NO_INDEX": "1", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                "PYTHONUTF8": "1", "LOCALAPPDATA": str(tmp)})
    return env


def free_letters(n):
    used = {d[0].upper() for d in os.listdrives()}
    preferred = [l for l in "EFG" if l not in used] + [l for l in "ZYXWVUTSRQPONMLKJIH" if l not in used]
    if len(preferred) < n:
        pytest.skip("no hay letras de unidad libres")
    return preferred[:n]


@contextmanager
def drive(letter, folder):
    subprocess.run(["subst", f"{letter}:", str(folder)], check=True, capture_output=True)
    try:
        yield Path(f"{letter}:\\")
    finally:
        subprocess.run(["subst", f"{letter}:", "/D"], capture_output=True)


def run(usb, env, *args, lines=None):
    p = subprocess.run(["cmd.exe", "/c", str(usb / "run.cmd"), *args], cwd=str(usb), env=env, timeout=1200,
                       input=("\n".join(lines) + "\n").encode("utf-8") if lines else b"", capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", errors="replace")
    return p.returncode, out


def assert_app_session(rc, out):
    assert rc == 0, out[-3000:]
    assert "ENVIRONMENT READY" in out
    assert "NVDA (suma = 13000)" in out            # lab question answered
    assert "n = 12" in out                         # Spark SQL from the menu
    assert "Traceback" not in out


def test_usb_offline_rehearsal(tmp_path):
    usb_folder = tmp_path / "usb" / "pyspark_lab_analyzer"
    copy_to_usb(usb_folder)
    assert not (usb_folder / ".venv").exists()
    size_mb = sum(f.stat().st_size for f in usb_folder.rglob("*") if f.is_file()) / 2**20
    print(f"\nTamano del kit copiado: {size_mb:.0f} MB")
    assert size_mb < 1024
    env = clean_env(tmp_path / "localappdata")
    (tmp_path / "localappdata").mkdir()
    first, second, third = free_letters(3)
    try:
        # 1) First drive letter: no .venv -> created from wheels\ without Internet -> READY.
        with drive(first, usb_folder) as usb:
            rc, out = run(usb, env, "--check", "--KitOnly", "--assume-yes")
            assert rc == 0, out
            assert "Creando entorno virtual" in out
            final_round = out[out.rindex("[1/9] Python"):]       # checks run again after the fix
            assert final_round.count("(kit USB)") == 2           # Python and Java from the USB
            assert "ENVIRONMENT READY" in out and f"Proyecto: {usb}" in out
            assert str(ROOT) not in out
            assert str(ROOT) not in (usb_folder / ".venv" / "pyvenv.cfg").read_text()

            # 2) Application: dataset + lab mode + Spark SQL, then a full restart.
            for _ in range(2):
                rc, out = run(usb, env, "--KitOnly", DATASET, "--lab", lines=APP_INPUT)
                assert_app_session(rc, out)
                assert "Creando entorno virtual" not in out      # the restart reuses the .venv
            rc, out = run(usb, env, "--KitOnly", lines=["salir"])   # no dataset given: it is asked
            assert rc == 0 and "Ingrese la ruta del dataset" in out

        # 3) The USB gets another letter: the old .venv is detected and recreated offline.
        with drive(second, usb_folder) as usb:
            rc, out = run(usb, env, "--check", "--KitOnly", "--assume-no")
            assert rc == 1 and "otro equipo" in out and "ENVIRONMENT NOT READY" in out
            assert "ENVIRONMENT READY" not in out.replace("ENVIRONMENT NOT READY", "")
            rc, out = run(usb, env, "--KitOnly", "--assume-yes", DATASET, "--lab", lines=APP_INPUT)
            assert_app_session(rc, out)

            # 4) A damaged Spark installation is NEVER reported as READY; --rebuild-venv repairs it.
            jar = next((usb_folder / ".venv" / "Lib" / "site-packages" / "pyspark" / "jars").glob("spark-core_*.jar"))
            jar.rename(jar.with_suffix(".jar.bak"))
            rc, out = run(usb, env, "--check", "--KitOnly")
            assert rc == 1 and "ENVIRONMENT NOT READY" in out and "--rebuild-venv" in out
            assert "ENVIRONMENT READY" not in out.replace("ENVIRONMENT NOT READY", "")
            rc, out = run(usb, env, "--rebuild-venv", "--check", "--KitOnly", "--assume-yes")
            assert rc == 0 and "ENVIRONMENT READY" in out, out[-3000:]

        # 5) A third letter, as on a different lab PC.
        with drive(third, usb_folder) as usb:
            rc, out = run(usb, env, "--KitOnly", "--assume-yes", DATASET, "--lab", lines=APP_INPUT)
            assert_app_session(rc, out)
            assert f"Proyecto: {usb}" in out
    finally:
        shutil.rmtree(usb_folder, ignore_errors=True)
