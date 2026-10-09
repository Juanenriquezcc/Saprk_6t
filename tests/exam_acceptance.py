"""Acceptance against the real exam (sales): reference ETL + its ten SQL queries, reproduced faithfully.

Source: https://github.com/camiloordo22vf/examen_1_herramientas_2026_2 (commit 4d545af), files
ventas_colombia_sucio.csv and salida_ventas_limpias/part-00000.parquet copied unchanged to
tests/data/examen/. The script etl_ventas_spark.py is NOT executed (external code that writes
files and needs pyarrow): its DataFrame operations are written again below, line by line, and
test_exam_acceptance.py checks the copy against the published Parquet.

Reference rules: header + inferSchema; city trim(lower) + mapping + initcap; product trim + 4
mappings by lower(); order_date with try_to_timestamp('yyyy-MM-dd HH:mm:ss') (other formats -> NULL,
row dropped) in [2025-01-01, 2026-01-01); 0 < quantity <= 20; 0 < unit_price_cop < 10.000.000;
18 <= customer_age <= 100; 1 <= shipping_days <= 15; 0 <= returned_qty <= quantity; every one
NOT NULL (and order_id, city, product); dropDuplicates(order_id); sales_cop = round(quantity *
unit_price_cop, 0). status, department, category, channel, payment_method and customer_type are
not normalized. The Parquet was written with pyarrow (clean.toArrow()), not with Spark's writer.

Run `python tests/exam_acceptance.py <salida.md>` for the acceptance matrix.
"""
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "examen"
CSV = DATA / "ventas_colombia_sucio.csv"
PARQUET = DATA / "salida_ventas_limpias.parquet"

# Quality rules of the reference written for the analyzer's ETL (explicit for this test only).
RULES = """
requerido: order_id, order_date, city, product, quantity, unit_price_cop, customer_age, shipping_days, returned_qty
order_date >= '2025-01-01' AND order_date < '2026-01-01'
quantity > 0 AND quantity <= 20
unit_price_cop > 0 AND unit_price_cop < 10000000
customer_age >= 18 AND customer_age <= 100
shipping_days >= 1 AND shipping_days <= 15
returned_qty >= 0 AND returned_qty <= quantity
derivada: sales_cop = round(quantity * unit_price_cop, 0)
"""
# ETL configuration of this case (phase 5.1): the reference maps 'Laptop Pro14' to 'Laptop Pro 14' by
# hand. The analyzer never does it on its own; it is an explicit equivalence the user confirms.
EQUIVALENCES = "equivalencia: product: Laptop Pro14 -> Laptop Pro 14\n"

# The ten queries of etl_ventas_spark.py, verbatim (view `ventas`).
QUERIES = [
    "SELECT product, SUM(quantity) AS unidades_vendidas FROM ventas WHERE status = 'Entregado' "
    "GROUP BY product ORDER BY unidades_vendidas DESC",
    "SELECT AVG(returned_qty) AS promedio_unidades_devueltas FROM ventas WHERE status = 'Devuelto'",
    "SELECT city, COUNT(order_id) AS pedidos_entregados FROM ventas WHERE status = 'Entregado' "
    "GROUP BY city ORDER BY pedidos_entregados DESC",
    "SELECT category, SUM(sales_cop) AS ingresos_cop FROM ventas WHERE status = 'Entregado' "
    "GROUP BY category ORDER BY ingresos_cop DESC",
    "SELECT channel, COUNT(order_id) AS pedidos_entregados FROM ventas WHERE status = 'Entregado' "
    "GROUP BY channel ORDER BY pedidos_entregados DESC",
    "SELECT AVG(sales_cop) AS promedio_venta_cop FROM ventas WHERE status = 'Entregado'",
    "SELECT department, SUM(quantity) AS unidades_vendidas FROM ventas WHERE status = 'Entregado' "
    "GROUP BY department ORDER BY unidades_vendidas DESC",
    "SELECT payment_method, COUNT(order_id) AS pedidos_entregados FROM ventas WHERE status = 'Entregado' "
    "GROUP BY payment_method ORDER BY pedidos_entregados DESC",
    "SELECT AVG(shipping_days) AS promedio_dias_envio FROM ventas WHERE status = 'Entregado'",
    "SELECT customer_type, SUM(sales_cop) AS ingresos_cop FROM ventas WHERE status = 'Entregado' "
    "GROUP BY customer_type ORDER BY ingresos_cop DESC",
]

# The same questions in Spanish, as a student would ask them, with the expected reading:
# (question, aggregation, measure, group column, status value).
QUESTIONS = [
    ("¿Cuántas unidades se vendieron por producto en pedidos entregados?", "SUM", "quantity", "product", "Entregado"),
    ("¿Cuál es el promedio de unidades devueltas en pedidos devueltos?", "AVG", "returned_qty", None, "Devuelto"),
    ("¿Cuántos pedidos entregados hay por ciudad?", "COUNT", None, "city", "Entregado"),
    ("¿Cuáles son los ingresos por categoría en pedidos entregados?", "SUM", "sales_cop", "category", "Entregado"),
    ("¿Cuántos pedidos entregados hay por canal?", "COUNT", None, "channel", "Entregado"),
    ("¿Cuál es el promedio de ventas por pedido entregado?", "AVG", "sales_cop", None, "Entregado"),
    ("¿Cuántas unidades se vendieron por departamento en pedidos entregados?", "SUM", "quantity", "department", "Entregado"),
    ("¿Cuántos pedidos entregados hay por método de pago?", "COUNT", None, "payment_method", "Entregado"),
    ("¿Cuál es el promedio de días de envío en pedidos entregados?", "AVG", "shipping_days", None, "Entregado"),
    ("¿Cuáles son los ingresos por tipo de cliente en pedidos entregados?", "SUM", "sales_cop", "customer_type", "Entregado"),
]


def exam_chooser(asked):
    """The student's decisions, explicit: 'ingresos' / 'ventas' is the column sales_cop of the exam.
    Any other question of the analyzer is recorded in `asked` and left unanswered (None = cancel)."""
    def choose(need):
        asked.append(need.key)
        if need.key.startswith("metric:"):
            return next((value for label, value in need.options if label == "Columna sales_cop"), None)
        return None
    return choose


def read_raw(spark):
    return spark.read.option("header", True).option("inferSchema", True).csv(str(CSV))


def reference_clean(spark):
    """etl_ventas_spark.py, lines 17-85, without the writes (same order of operations)."""
    from pyspark.sql.functions import col, initcap, lit, lower, trim, try_to_timestamp, when
    from pyspark.sql.functions import round as spark_round

    df = read_raw(spark)
    df = df.withColumn("city", trim(lower(col("city"))))
    df = df.withColumn(
        "city",
        when(col("city").isin("bogota", "bogotá"), "Bogotá")
        .when(col("city") == "medellin", "Medellín")
        .when(col("city") == "cali", "Cali")
        .when(col("city").isin("pasto"), "Pasto")
        .when(col("city").isin("barranquilla"), "Barranquilla")
        .when(col("city").isin("cartagena"), "Cartagena")
        .otherwise(initcap(col("city"))))
    df = df.withColumn("product", trim(col("product")))
    df = df.withColumn(
        "product",
        when(lower(col("product")) == "smartphone x", "Smartphone X")
        .when(lower(col("product")) == "laptop pro14", "Laptop Pro 14")
        .when(lower(col("product")) == "audifonos bluetooth", "Audífonos Bluetooth")
        .when(lower(col("product")) == "mouse inalambrico", "Mouse Inalámbrico")
        .otherwise(col("product")))
    df = df.withColumn("order_date_ts", try_to_timestamp(col("order_date"), lit("yyyy-MM-dd HH:mm:ss")))
    clean = df.filter(
        col("order_id").isNotNull() &
        col("order_date_ts").isNotNull() &
        (col("order_date_ts") >= "2025-01-01") &
        (col("order_date_ts") < "2026-01-01") &
        col("city").isNotNull() &
        col("product").isNotNull() &
        col("quantity").isNotNull() &
        (col("quantity") > 0) &
        (col("quantity") <= 20) &
        col("unit_price_cop").isNotNull() &
        (col("unit_price_cop") > 0) &
        (col("unit_price_cop") < 10000000) &
        col("customer_age").isNotNull() &
        (col("customer_age") >= 18) &
        (col("customer_age") <= 100) &
        col("shipping_days").isNotNull() &
        (col("shipping_days") >= 1) &
        (col("shipping_days") <= 15) &
        col("returned_qty").isNotNull() &
        (col("returned_qty") >= 0) &
        (col("returned_qty") <= col("quantity"))
    ).dropDuplicates(["order_id"])
    return clean.withColumn("sales_cop", spark_round(col("quantity") * col("unit_price_cop"), 0))


def run_reference(spark, df, query, view="referencia_examen"):
    """Rows of a reference query over `df` (bounded: at most 100 groups). The view is its own and is
    dropped afterwards, so it never takes a name a test registers (such as 'ventas')."""
    df.createOrReplaceTempView(view)
    try:
        return [tuple(r) for r in spark.sql(query.replace("FROM ventas", f"FROM {view}")).limit(100).collect()]
    finally:
        spark.catalog.dropTempView(view)


def as_result(rows):
    """{group: value} for grouped results, or the single value."""
    if rows and len(rows[0]) == 1:
        return rows[0][0]
    return {r[0]: r[1] for r in rows}


def same(a, b, rel=1e-9):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], rel) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= rel * max(1.0, abs(float(a)), abs(float(b)))
    return a == b


def descending(rows):
    values = [r[-1] for r in rows]
    return all(x >= y for x, y in zip(values, values[1:]))


# --- the analyzer side ----------------------------------------------------------------------------

def session_on(spark, df, name):
    """LabSession over an existing DataFrame (the analyzer's own profile, roles and interpreter)."""
    from app.session import LabSession
    from app.spark.loader import LoadResult
    df.createOrReplaceTempView("dataset")
    rows = df.count()
    return LabSession.start(spark, LoadResult(df=df, path=Path(name), fmt="csv", rows=rows, columns=df.columns))


def analyzer_etl(spark, rules=RULES, confirm_equivalences=None):
    """Original CSV -> the analyzer's loader and ETL with the given rules (explicit decisions).
    confirm_equivalences: answer to every 'equivalence:...' question (None = none may be asked)."""
    from app.etl import pipeline
    from app.etl.rules import parse_rules
    from app.session import LabSession
    from app.spark.loader import load_dataset, sniff_csv
    load = load_dataset(spark, CSV, "csv", sniff_csv(CSV))
    answers = {"transform": "all", "unknown": True, "duplicates": True}   # apply cleaning, reject NULLs, dedupe

    def decide(d):
        if d.key.startswith("equivalence:") and confirm_equivalences is not None:
            return confirm_equivalences
        return answers[d.key]
    report = pipeline.run(spark, load, parse_rules(rules), decide, "reglas de referencia del examen")
    return LabSession.start(spark, load, report)


def ask_exam(session, question):
    """(Evidence or None, [keys the analyzer asked], result) - None when the analyzer cannot answer."""
    from app.questions.intents import NeedsInput
    session.df.createOrReplaceTempView("dataset")
    asked = []
    ev = session.ask(question, exam_chooser(asked))
    if ev is None or isinstance(ev, NeedsInput):
        return None, asked, getattr(ev, "message", "pregunta cancelada")
    result = ev.value if ev.spec["shape"] == "SCALAR" else as_result([tuple(r) for r in ev.result_rows])
    return ev, asked, result


# --- matrix (python tests/exam_acceptance.py salida.md) ---------------------------------------------

def _short(value):
    if isinstance(value, dict):
        top = list(value.items())[:3]
        return "; ".join(f"{k}: {_num(v)}" for k, v in top) + (f" (+{len(value) - 3} grupos)" if len(value) > 3 else "")
    return _num(value)


def _num(v):
    return f"{v:,.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def main(out):
    import time
    import conftest  # noqa: F401  (JAVA_HOME of the kit, as in the tests)
    from app.spark.session import get_spark, stop_spark

    spark = get_spark(master="local[2]")
    jvm = spark.sparkContext._jvm.java.lang.Runtime.getRuntime()
    t0 = time.perf_counter()
    reference = reference_clean(spark).cache()
    ref_rows = reference.count()
    t_ref = time.perf_counter() - t0
    raw = read_raw(spark)
    t0 = time.perf_counter()
    etl_session = analyzer_etl(spark, RULES + EQUIVALENCES, confirm_equivalences=True)
    t_etl = time.perf_counter() - t0
    ref_session = session_on(spark, reference, "referencia_limpia")
    lines = ["# Matriz de aceptacion: examen de ventas", "",
             f"Registros originales: {raw.count()} | referencia limpia: {ref_rows} | ETL del analizador: "
             f"{etl_session.etl.valid_rows} (rechazados {etl_session.etl.rejected_rows}, duplicados eliminados "
             f"{etl_session.etl.duplicates_removed})", "",
             "| # | Pregunta | QuerySpec | SQL del analizador | Referencia | Analizador (datos de referencia) | B | "
             "Analizador (su ETL) | A | Causa |", "|" + "---|" * 10]
    t_questions = 0.0
    for i, (question, *_rest) in enumerate(QUESTIONS):
        expected = as_result(run_reference(spark, reference, QUERIES[i]))
        t0 = time.perf_counter()
        ev_b, _, got_b = ask_exam(ref_session, question)
        t_questions += time.perf_counter() - t0
        ev_a, _, got_a = ask_exam(etl_session, question)
        state_b = "NO ADMITIDA" if ev_b is None else ("PASS" if same(expected, got_b) else "FAIL")
        state_a = "NO ADMITIDA" if ev_a is None else ("PASS" if same(expected, got_a) else "FAIL")
        cause = "" if state_a == "PASS" else "diferencia del ETL (ver informe)"
        spec = ev_b.spec if ev_b else {}
        qs = (f"{spec.get('aggregation')}({', '.join((spec.get('target') or {}).get('columns') or ['*'])}) "
              f"por {spec.get('group_by') or '-'}; {', '.join(f['column'] + ' = ' + str(f['value']) for f in spec.get('filters', []))}"
              if spec else "-")
        sql = ev_b.sql.replace("\n", " ") if ev_b else "-"
        lines.append(f"| {i + 1} | {question} | {qs} | `{sql}` | {_short(expected)} | {_short(got_b)} | {state_b} | "
                     f"{_short(got_a)} | {state_a} | {cause} |")
    used = (jvm.totalMemory() - jvm.freeMemory()) / 2 ** 20
    lines += ["", f"Tiempos: ETL de referencia {t_ref:.1f} s | ETL del analizador (carga + ETL + perfil) {t_etl:.1f} s | "
                  f"10 preguntas sobre la referencia {t_questions:.1f} s. Memoria JVM usada al final: {used:.0f} MB "
                  f"(maximo configurado {jvm.maxMemory() / 2 ** 20:.0f} MB)."]
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    stop_spark()


if __name__ == "__main__":
    import sys
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
    main(sys.argv[1] if len(sys.argv) > 1 else "aceptacion_examen.md")
