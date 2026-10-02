"""Environment diagnosis from Python: `python main.py --check [--json]`.

run.ps1 runs it with --json and takes the SparkSession and Spark SQL results from here.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import config

JSON_MARKER = "##LABCHECK##"


def _result(name, status, detail, hint=""):
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def check_python():
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if v[:2] >= config.MIN_PYTHON:
        return _result("Python", "OK", detail)
    return _result("Python", "ERROR", detail, f"Se requiere Python {'.'.join(map(str, config.MIN_PYTHON))} o superior.")


def java_major(version_output):
    match = re.search(r'version "(\d+)(?:\.(\d+))?', version_output)
    if not match:
        return None
    major = int(match.group(1))
    return int(match.group(2)) if major == 1 and match.group(2) else major  # "1.8" -> 8


def check_java():
    java_home = os.environ.get("JAVA_HOME")
    exe = Path(java_home, "bin", "java.exe" if os.name == "nt" else "java") if java_home else shutil.which("java")
    if not exe or not Path(exe).exists():
        return _result("Java", "ERROR", "No se encontro Java (JAVA_HOME vacio y java fuera del PATH).",
                       "Ejecute run.cmd: configura JAVA_HOME para el proceso con el JRE del kit.")
    try:
        out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _result("Java", "ERROR", f"No se pudo ejecutar {exe}: {exc}")
    major = java_major(out.stderr + out.stdout)
    if major in config.SUPPORTED_JAVA:
        return _result("Java", "OK", f"Java {major} ({exe})")
    versions = "/".join(map(str, config.SUPPORTED_JAVA))
    return _result("Java", "ERROR", f"Java {major} en {exe} no es compatible.", f"Spark 4 requiere Java {versions}.")


def pinned_pyspark_version():
    try:
        text = config.REQUIREMENTS_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^pyspark==(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def check_pyspark():
    try:
        import pyspark
    except ImportError:
        return _result("PySpark", "ERROR", "PySpark no esta instalado.", "Ejecute run.cmd para instalarlo desde wheels\\.")
    pinned = pinned_pyspark_version()
    if pinned and pyspark.__version__ != pinned:
        return _result("PySpark", "ERROR", f"Version {pyspark.__version__}; se requiere {pinned}.",
                       "Ejecute run.cmd para reinstalarlo desde wheels\\.")
    return _result("PySpark", "OK", pyspark.__version__)


def check_spark():
    """Starts Spark, runs SQL and reads a temporary CSV. Returns [SparkSession, Spark SQL]."""
    from app.errors import AppError, short_error
    from app.spark.session import get_spark, stop_spark

    start = time.perf_counter()
    try:
        spark = get_spark()
    except AppError as exc:
        return [_result("SparkSession", "ERROR", exc.message, exc.hint or ""),
                _result("Spark SQL", "SKIP", "requiere SparkSession")]
    try:
        ansi = spark.conf.get("spark.sql.ansi.enabled")
        partitions = spark.conf.get("spark.sql.shuffle.partitions")
        session = _result("SparkSession", "OK", f"Spark {spark.version}, arranque {time.perf_counter() - start:.1f} s, "
                                                f"ANSI={ansi}, shuffle.partitions={partitions}")
        try:
            select_ok = spark.sql("SELECT 1 AS ok").first()["ok"] == 1
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                csv_path = Path(tmp, "check.csv")
                csv_path.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
                rows = spark.read.option("header", True).csv(str(csv_path)).count()
            if select_ok and rows == 3:
                sql = _result("Spark SQL", "OK", "SELECT 1 y lectura de CSV correctas")
            else:
                sql = _result("Spark SQL", "ERROR", f"Resultados inesperados (SELECT={select_ok}, filas CSV={rows}).")
        except Exception as exc:
            sql = _result("Spark SQL", "ERROR", short_error(exc), "Ejecute run.cmd -CheckOnly con --debug para mas detalle.")
        return [session, sql]
    finally:
        stop_spark()


def run_checks():
    results = [check_python(), check_java(), check_pyspark()]
    if results[-1]["status"] == "OK":
        results += check_spark()
    else:
        results += [_result("SparkSession", "SKIP", "requiere PySpark"), _result("Spark SQL", "SKIP", "requiere PySpark")]
    return results


def print_report(results, as_json=False):
    total = len(results)
    for i, r in enumerate(results, 1):
        print(f"[{i}/{total}] {r['name'] + ' ':.<24} {r['status']:<6} {r['detail']}")
        if r["hint"] and r["status"] != "OK":
            print(f"        -> {r['hint']}")
    if as_json:
        print(JSON_MARKER + json.dumps(results, ensure_ascii=True))
