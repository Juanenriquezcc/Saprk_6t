"""Static audits of the project sources: absolute paths, Internet access and memory-unsafe APIs.

These tests read the source files only (fast, no Spark).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {".venv", "tools", "wheels", "exports", "__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".ps1", ".cmd", ".md", ".ini", ".txt", ".json", ".cfg", ".toml"}

# Files that run during the lab (run.cmd -> run.ps1 -> bootstrap -> main.py -> app/).
RUNTIME = ([ROOT / "run.cmd", ROOT / "run.ps1", ROOT / "main.py", ROOT / "config.py",
            ROOT / "bootstrap" / "checks.ps1", ROOT / "bootstrap" / "install.ps1"]
           + sorted((ROOT / "app").rglob("*.py")))
PREPARE_KIT = ROOT / "bootstrap" / "prepare_kit.ps1"


def project_files():
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not GENERATED & set(rel.parts) \
                and rel.parts[:2] != ("tests", "data"):
            yield path


def code_lines(path):
    """Lines without comments (# in Python/PowerShell, rem in cmd)."""
    for n, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "rem ", "REM ")):
            continue
        yield n, line


# --- 1. absolute paths ------------------------------------------------------------

ABSOLUTE = re.compile(r"(?<![\w%$])[A-Za-z]:[\\/]|\\\\[\w.-]+\\|(?i:program ?files|programdata)(?![\w(])|/home/|/Users/")
ALLOWED_FAKE_PATHS = {  # fake paths / patterns used only to detect the Microsoft Store alias
    ("tests/test_bootstrap.py", r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"),
    ("tests/test_bootstrap.py", r"C:\Python312\python.exe"),
    ("bootstrap/checks.ps1", r"'\\Microsoft\\WindowsApps\\'"),
    ("README.md", r"E:\pyspark_lab_analyzer"),     # documented example: E: = the USB letter
    ("MANUAL_USUARIO.md", r"E:\pyspark_lab_analyzer"),
}
SELF = "tests/test_audit.py"   # this file contains the patterns themselves


def test_no_absolute_paths_in_project():
    found = []
    for path in project_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel == SELF:
            continue
        for n, line in code_lines(path):
            for m in ABSOLUTE.finditer(line):
                if any(rel == f and fake in line for f, fake in ALLOWED_FAKE_PATHS):
                    continue
                if "$env:" in line or "${env:" in line or "%LOCALAPPDATA%" in line:
                    continue   # resolved from the environment of the current PC
                found.append(f"{rel}:{n}: {line.strip()}")
    assert not found, "Rutas absolutas:\n" + "\n".join(found)


def test_runtime_paths_come_from_the_project_folder():
    config = (ROOT / "config.py").read_text(encoding="utf-8")
    assert 'PROJECT_ROOT = Path(__file__).resolve().parent' in config
    run_ps1 = (ROOT / "run.ps1").read_text(encoding="utf-8-sig")
    assert "$Root = $PSScriptRoot" in run_ps1
    assert '"%~dp0run.ps1"' in (ROOT / "run.cmd").read_text(encoding="ascii")


# --- 2. Internet -----------------------------------------------------------------

NETWORK = re.compile(r"https?://|\burllib\b|\brequests\b|http\.client|\bsocket\b|Invoke-WebRequest|"
                     r"Invoke-RestMethod|WebClient|DownloadFile|\bopenai\b|\banthropic\b|\bwinget\b|"
                     r"\bcurl\b|\bwget\b|BITS|Start-BitsTransfer", re.IGNORECASE)


def test_runtime_files_never_use_the_network():
    found = []
    for path in RUNTIME:
        for n, line in code_lines(path):
            if NETWORK.search(line):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not found, "Acceso a red en archivos de ejecucion:\n" + "\n".join(found)


def test_pip_only_installs_from_the_local_wheels():
    for path in RUNTIME:
        for n, line in code_lines(path):
            if re.search(r"-m\s+pip\s+install", line):   # real invocations (not message texts)
                assert "--no-index" in line and "--find-links" in line, f"{path.name}:{n}: {line.strip()}"


def test_downloads_exist_only_in_prepare_kit_and_only_behind_its_switch():
    assert NETWORK.search(PREPARE_KIT.read_text(encoding="utf-8-sig"))
    run_ps1 = (ROOT / "run.ps1").read_text(encoding="utf-8-sig")
    block = run_ps1[run_ps1.index("if ($PrepareKit) {"):]
    block = block[: block.index("}") + 1]
    assert "prepare_kit.ps1" in block and run_ps1.count("prepare_kit.ps1") == 1


def test_runtime_dependencies_are_only_pyspark():
    reqs = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip()]
    assert reqs == ["pyspark==4.2.0"]


# --- 3. memory: nothing brings the whole dataset to Python --------------------------------

FORBIDDEN = re.compile(r"toPandas|\.rdd\b|toLocalIterator|\budf\b|pandas_udf|\.toJSON\(|\.foreach\(")


def test_no_memory_unsafe_spark_apis():
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        for n, line in code_lines(path):
            if FORBIDDEN.search(line):
                found.append(f"{path.name}:{n}: {line.strip()}")
            if ".collect()" in line and ".limit(" not in line:
                found.append(f"{path.name}:{n}: collect() sin limit(): {line.strip()}")
    assert not found, "\n".join(found)
