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


# --- 500k DIRTY rows through the whole workshop: ETL + rules + questions + export --------------

CITIES = ["Bogota"] * 4 + ["Medellin"] * 3 + ["Cali"] * 2 + ["Pasto", "Barranquilla"]
STATUS = ["Entregado", "Entregado", "Entregado", "Devuelto", "Cancelado"]


@pytest.fixture(scope="module")
def dirty_csv(tmp_path_factory):
    """Dirty sales data plus an independent oracle computed while writing it (test data only)."""
    path = tmp_path_factory.mktemp("big") / "ventas_500k_sucio.csv"
    oracle = {"rows": 0, "valid": 0, "duplicates": 0, "delivered": {}, "quantity": 0}
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("order_id;order_date;city;quantity;unit_price_cop;discount;status\n")
        for i in range(ROWS):
            city = CITIES[i * 7 % 11]
            written_city = city.upper() + " " if i % 13 == 0 else (
                {"Bogota": "Bogotá", "Medellin": "Medellín"}.get(city, city) if i % 17 == 0 else city)
            status = STATUS[i % 5]
            written_status = "N/A" if i % 23 == 0 else ("entregado" if i % 19 == 0 and status == "Entregado" else status)
            quantity = 25 if i % 500 == 0 else 1 + i % 20
            written_q = "abc" if i % 1000 == 7 else str(quantity)
            price = 1000 * (5 + i % 300)
            date = f"2025-{1 + i // 100 % 12:02d}-{1 + i % 28:02d}" if i % 2 else f"{1 + i % 28:02d}/{1 + i // 100 % 12:02d}/2025"
            written_price = f"{price:,}".replace(",", ".")          # Spanish thousands: 12.000
            line = f"{i};{date};{written_city};{written_q};{written_price};0,{i % 3};{written_status}\n"
            copies = 2 if i % 10000 == 1 else 1
            f.write(line * copies)
            oracle["rows"] += copies
            if quantity > 20 or written_q == "abc":
                continue
            oracle["valid"] += 1                  # duplicates removed: one copy stays
            oracle["duplicates"] += copies - 1
            oracle["quantity"] += quantity
            if written_status != "N/A" and status == "Entregado":
                oracle["delivered"][city] = oracle["delivered"].get(city, 0) + 1
    return path, oracle


def test_500k_dirty_workshop(spark, dirty_csv, tmp_path):
    from app.etl import pipeline
    from app.etl.rules import parse_rules
    from app.export import export_workshop
    from app.session import LabSession
    from app.spark.loader import load_dataset, resolve_path, sniff_csv

    path, oracle = dirty_csv
    timings = []

    def timed(label, fn):
        start = time.perf_counter()
        out = fn()
        timings.append((label, time.perf_counter() - start))
        return out

    rss_start = python_rss_mb()
    load = timed("carga", lambda: load_dataset(spark, resolve_path(str(path)), "csv", sniff_csv(path)))
    assert load.rows == oracle["rows"]
    answers = {"transform": "all", "unknown": True, "duplicates": True, "number:unit_price_cop": "thousands"}
    report = timed("ETL (transformar + validar + cargar)",
                   lambda: pipeline.run(spark, load, parse_rules("quantity entre 1 y 20"), lambda d: answers[d.key]))
    assert report.valid_rows == oracle["valid"] and report.duplicates_removed == oracle["duplicates"]
    session = timed("perfilado", lambda: LabSession.start(spark, load, report))

    solve = lambda q, sel=None: session.solve_question(q, chooser({}), selected=sel)   # noqa: E731
    expected_city = max(oracle["delivered"], key=oracle["delivered"].get)
    ev = timed("ciudad con mas pedidos entregados", lambda: solve("¿Cuál ciudad tiene más pedidos entregados?", expected_city))
    assert ev.value == expected_city and ev.validation == "CORRECTA"
    ev = timed("conteo", lambda: solve("¿Cuántos registros hay?", str(oracle["valid"])))
    assert ev.validation == "CORRECTA"
    ev = timed("suma", lambda: solve("¿Cuál es el total de quantity?"))
    assert ev.value == oracle["quantity"]
    ev = timed("V/F comparativa", lambda: solve("¿El promedio de unit_price_cop es mayor a 100000? V/F", "V"))
    assert ev.verdict == "VERDADERO"          # prices 5.000 .. 304.000 read as thousands
    ev = timed("por mes acumulado", lambda: solve("Cantidad total por mes acumulada"))
    assert ev.result_rows[-1][2] == oracle["quantity"] and len(ev.result_rows) == 12
    folder = timed("exportacion", lambda: export_workshop(session, tmp_path))
    rss_end = python_rss_mb()
    print(f"\n{'operacion (500k sucio)':<42} {'segundos':>8}")
    for label, seconds in timings:
        print(f"{label:<42} {seconds:>8.2f}")
    print(f"RSS Python inicio/fin: {rss_start:.0f} / {rss_end:.0f} MB; rechazados {report.rejected_rows}")
    assert len(list(folder.iterdir())) == 6
    assert rss_end - rss_start < 80              # rows never travel to Python
    report.df.unpersist()
