"""Smoke test with 500,000 rows: load, profile and the main questions through the engine.

Checks there is no design error that makes Python process the rows: the Python process
memory must stay flat while Spark answers. Run with -s to see the timing table.
"""
import ctypes
import os
import time
from ctypes import wintypes

import pytest

from conftest import chooser

ROWS = 500_000
pytestmark = pytest.mark.slow


def python_rss_mb():
    if os.name != "nt":
        return 0.0

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 2**20


@pytest.fixture(scope="module")
def big_csv(tmp_path_factory):
    """Deterministic CSV written line by line (test data generation, not engine code)."""
    path = tmp_path_factory.mktemp("big") / "market_500k.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("trade_date,ticker,open_price,high_price,low_price,close_price,shares\n")
        for i in range(ROWS):
            day = 1 + i // 50 % 28
            month = 1 + i // 1400 % 12
            year = 2015 + i // 16800 % 10
            o = 50 + (i * 7919 % 10007) / 10
            c = o + ((i * 31 % 21) - 10) / 4
            f.write(f"{year}-{month:02d}-{day:02d},T{i % 50:02d},{o:.2f},{max(o, c) + 1.5:.2f},"
                    f"{min(o, c) - 1.25:.2f},{c:.2f},{1000 + i * 37 % 90001}\n")
    return path


def test_500k_rows(spark, big_csv):
    import config
    from pyspark.sql import functions as F

    from app.session import LabSession
    from app.spark.loader import detect_format, load_dataset, resolve_path, sniff_csv

    timings = []

    def timed(label, fn):
        start = time.perf_counter()
        out = fn()
        timings.append((label, time.perf_counter() - start))
        return out

    path = resolve_path(str(big_csv))
    load = timed("carga (inferSchema + cache + count)",
                 lambda: load_dataset(spark, path, detect_format(path), sniff_csv(path)))
    assert load.rows == ROWS
    session = timed("perfilado (1 pasada + categorias)", lambda: LabSession.start(spark, load))
    assert session.semantic.resolved("CLOSE") == "close_price"
    assert session.semantic.resolved("COMPANY") == "ticker"
    assert session.profile.column("ticker").is_categorical and len(session.profile.column("ticker").values) == 50

    df = load.df
    rss_before = python_rss_mb()
    ask = lambda q: session.ask(q, chooser({}))   # noqa: E731

    ev = timed("COUNT", lambda: ask("¿Cuántos registros hay?"))
    assert ev.value == ROWS

    ev = timed("AVG", lambda: ask("¿Cuál es el precio promedio de cierre?"))
    assert ev.value == pytest.approx(df.agg(F.avg("close_price")).first()[0])

    ev = timed("GROUP BY (promedio por ticker)", lambda: ask("Promedio de cierre por ticker"))
    assert len(ev.result_rows) == 50

    ev = timed("TOP (ticker con mayor volumen total)", lambda: ask("¿Qué ticker tiene el mayor volumen total?"))
    best = df.groupBy("ticker").agg(F.sum("shares").alias("s")).orderBy(F.desc("s"), "ticker").first()
    assert ev.value == best["ticker"]

    ev = timed("registro extremo (mayor volumen)", lambda: ask("¿En qué fecha se registró el mayor volumen?"))
    assert str(ev.value)[:10] == str(df.orderBy(F.desc("shares")).first()["trade_date"])

    ev = timed("COUNT WHERE (cierre > apertura)", lambda: ask("¿Cuántos registros tienen cierre mayor que apertura?"))
    assert ev.value == df.filter(F.col("close_price") > F.col("open_price")).count()

    ev = timed("variacion % promedio", lambda: ask("¿Cuál es la variación porcentual promedio entre apertura y cierre?"))
    expected = df.agg(F.avg((F.col("close_price") - F.col("open_price")) / F.col("open_price") * 100)).first()[0]
    assert ev.value == pytest.approx(expected)

    timed("variacion % del periodo por ticker",
          lambda: ask("¿Qué ticker tuvo la mayor variación porcentual del cierre en el periodo?"))

    # Phase 3 tools over the same 500k rows.
    from app.analysis import full, guided
    ev = timed("SQL manual: SELECT * sin LIMIT (acotado)", lambda: session.run_sql("SELECT * FROM dataset"))
    assert len(ev.result_rows) == config.MANUAL_SQL_MAX_ROWS and "hay mas filas" in ev.result_text
    ev = timed("SQL manual: GROUP BY", lambda: session.run_sql(
        "SELECT ticker, AVG(close_price) AS p FROM dataset GROUP BY ticker ORDER BY p DESC"))
    assert len(ev.result_rows) == config.MANUAL_SQL_MAX_ROWS
    ev = timed("guiado: estadisticas de columna",
               lambda: session.run_spec(guided.column_stats(session.profile, "close_price"), "stats"))
    assert ev.result_rows[0][1] == ROWS
    timed("guiado: ranking top 5",
          lambda: session.run_spec(guided.ranking("ticker", "AVG", "shares", "DESC", 5), "ranking"))
    results = timed("analisis completo", lambda: full.run(session))
    assert all(r.error is None for r in results) and len(results) <= config.FULL_ANALYSIS_MAX_STEPS
    rss_after = python_rss_mb()

    jvm = spark.sparkContext._jvm.java.lang.Runtime.getRuntime()
    jvm_used = (jvm.totalMemory() - jvm.freeMemory()) / 2**20
    print(f"\n{'operacion':<42} {'segundos':>8}")
    for label, seconds in timings:
        print(f"{label:<42} {seconds:>8.2f}")
    print(f"RSS Python antes/despues de las preguntas: {rss_before:.0f} / {rss_after:.0f} MB")
    print(f"Memoria JVM usada: {jvm_used:.0f} MB (driver configurado: {config.SPARK_CONF['spark.driver.memory']})")

    # The engine never brings the 500k rows to Python: its memory must stay flat.
    assert rss_after - rss_before < 50
    load.df.unpersist()
