"""ETL: transform, validate, rules, duplicates and the views it registers.

Expected values are counted by hand from tests/data/ventas_sucio.csv (16 rows):
  rejected by rules: 8 (quantity 25), 9 (age 17), 10 (returned 9 > 8), 11 (year 2024)
  invalid values:    12 (quantity 'abc'), 15 ('fecha mala')
  unknown (NULL):    16 (age empty) -> rejected only when the user says so
  duplicate:         row 13 appears twice
"""
import datetime

import pytest

from app.errors import AppError
from app.etl import pipeline
from app.etl.rules import CHECK, DERIVED, parse_rules
from app.spark.loader import load_dataset, resolve_path, sniff_csv
from conftest import DATA

LAB_RULES = """# reglas del taller
quantity entre 1 y 20
customer_age BETWEEN 18 AND 100
returned_qty <= quantity
año(order_date) = 2025
derivada: ingresos = quantity * unit_price_cop
"""


def load(spark, name="ventas_sucio.csv"):
    path = resolve_path(str(DATA / name))
    return load_dataset(spark, path, "csv", sniff_csv(path))


def answers(**values):
    asked = []

    def decide(decision):
        asked.append(decision.key)
        if decision.key not in values:
            raise AssertionError(f"Unexpected decision: {decision.key} {decision.message}")
        return values[decision.key]
    decide.asked = asked
    return decide


def test_parse_rules_shortcuts():
    rules = parse_rules(LAB_RULES + "requerido: city, product\nstatus IN ('Entregado', 'Devuelto');\n")
    assert [r.expr for r in rules if r.kind == CHECK] == [
        "quantity BETWEEN 1 AND 20", "customer_age BETWEEN 18 AND 100", "returned_qty <= quantity",
        "year(order_date) = 2025", "city IS NOT NULL", "product IS NOT NULL", "status IN ('Entregado', 'Devuelto')"]
    derived = [r for r in rules if r.kind == DERIVED]
    assert [(r.name, r.expr) for r in derived] == [("ingresos", "quantity * unit_price_cop")]


def test_full_etl_on_dirty_data(spark):
    decide = answers(transform="all", unknown=True, duplicates=True)
    report = pipeline.run(spark, load(spark), parse_rules(LAB_RULES), decide)
    assert decide.asked == ["transform", "unknown", "duplicates"]
    assert (report.original_rows, report.rejected_rows, report.duplicates_removed, report.valid_rows) == (16, 7, 1, 8)
    assert report.invalid_values == {"order_date": 1, "quantity": 1}
    assert [(r.failed, r.unknown) for r in report.rule_results] == [(1, 1), (1, 1), (1, 1), (1, 1)]

    df = spark.sql("SELECT * FROM dataset")
    types = dict(df.dtypes)
    assert types["quantity"] == "bigint" and types["unit_price_cop"] == "double" and types["order_date"] == "date"
    rows = {r["order_id"]: r for r in df.collect()}            # 8 rows: tiny test fixture
    assert sorted(rows) == [1, 2, 3, 4, 5, 6, 7, 13]
    assert rows[1]["unit_price_cop"] == 2_500_000 and rows[1]["discount"] == pytest.approx(0.1)
    assert rows[2]["order_date"] == datetime.date(2025, 2, 15)      # 15/02/2025, day first
    assert {rows[i]["city"] for i in (1, 2, 3)} == {"Bogota"}       # Bogota / Bogotá / 'BOGOTA '
    assert rows[4]["city"] == "Medellin" and rows[4]["status"] == "Entregado"
    assert rows[13]["status"] is None                               # 'N/A'
    assert rows[1]["ingresos"] == 5_000_000

    rejected = {r["order_id"]: r["motivo_rechazo"] for r in spark.sql("SELECT * FROM rechazados").collect()}
    assert sorted(rejected) == [8, 9, 10, 11, 12, 15, 16]
    assert "valor no valido en quantity" in rejected[12]
    assert spark.sql("SELECT COUNT(*) FROM dataset_original").first()[0] == 16
    data = report.to_dict()
    assert data["registros_validos"] == 8 and data["valores_no_validos"]["quantity"] == 1


def test_unknown_rows_can_be_kept_and_duplicates_kept(spark):
    report = pipeline.run(spark, load(spark), parse_rules(LAB_RULES),
                          answers(transform="all", unknown=False, duplicates=False))
    assert (report.rejected_rows, report.duplicates_found, report.duplicates_removed, report.valid_rows) == (6, 1, 0, 10)


def test_transformations_can_be_refused(spark):
    report = pipeline.run(spark, load(spark), [], answers(transform="none", duplicates=False))
    assert report.transformations == [] and report.rejected_rows == 0 and report.valid_rows == 16
    assert dict(spark.sql("SELECT * FROM dataset").dtypes)["quantity"] == "string"


def test_clean_dataset_needs_no_decision(spark):
    data = load(spark, "stocks_small.csv")
    report = pipeline.run(spark, data, [], answers())
    assert not report.changed and report.df is data.df and report.valid_rows == 12


def test_ambiguous_numbers_and_dates_are_asked(spark, tmp_path):
    path = tmp_path / "ambiguo.csv"
    path.write_text("fecha;monto\n03/04/2025;1.234\n05/06/2025;2.500\n01/02/2025;3.000\n", encoding="utf-8")
    loaded = load_dataset(spark, resolve_path(str(path)), "csv", sniff_csv(path))
    decide = answers(transform="all", **{"date:fecha": "mdy", "number:monto": "thousands"})
    pipeline.run(spark, loaded, [], decide)
    assert set(decide.asked) == {"transform", "date:fecha", "number:monto"}
    row = spark.sql("SELECT * FROM dataset ORDER BY monto").first()
    assert row["fecha"] == datetime.date(2025, 3, 4) and row["monto"] == 1234

    # The same file read as decimals: Spark's value is kept, no transformation is listed.
    loaded = load_dataset(spark, resolve_path(str(path)), "csv", sniff_csv(path))
    report = pipeline.run(spark, loaded, [], answers(transform="all", **{"date:fecha": "dmy", "number:monto": "decimal"}))
    assert [t.kind for t in report.transformations] == ["date"]
    row = spark.sql("SELECT * FROM dataset ORDER BY monto").first()
    assert row["fecha"] == datetime.date(2025, 4, 3) and row["monto"] == pytest.approx(1.234)


def test_invalid_rule_is_reported(spark):
    with pytest.raises(AppError, match="no es valida"):
        pipeline.run(spark, load(spark), parse_rules("cantidad_inexistente > 0"), answers(transform="all"))
    with pytest.raises(AppError, match="no es una condicion"):
        pipeline.run(spark, load(spark), parse_rules("quantity + 1"), answers(transform="all"))


def test_decision_without_user_is_never_guessed(spark):
    with pytest.raises(AppError, match="necesita una decision"):
        pipeline.run(spark, load(spark), [])
