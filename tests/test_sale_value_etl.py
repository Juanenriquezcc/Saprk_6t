"""The real flow (loader + ETL screen) offers the sale value column: 'valor de venta' needs sales_cop.

Root cause of the defect: the ETL creates derived columns ONLY from declared rules ('derivada: ...').
Loading ventas_colombia_sucio.csv and pressing Enter at the rules prompt left no sales_cop, so
'valor de venta' had no column (the earlier test passed because it supplied the rule itself).
Now the ETL screen OFFERS the reference formula sales_cop = round(quantity * unit_price_cop, 0)
(no discount) when the data has one quantity, one unit price and no amount; the user confirms it.
Expected values are computed here with Spark over the cleaned data, not with the analyzer's SQL.
"""
import pytest
from pyspark.sql import functions as F

import exam_acceptance as ex
from app.catalog import DatasetCatalog
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from app.session import LabSession
from app.spark.loader import load_dataset, sniff_csv
from app.ui import etl as etl_ui
from app.ui.etl import sale_value_rule
from conftest import DATA, chooser

QUESTION = "¿Cuál es el promedio del valor de venta por pedido Entregado?"


def real_etl(spark, monkeypatch, capsys, inputs):
    """load_dataset + the ETL screen exactly as the program runs them, with typed answers."""
    load = load_dataset(spark, ex.CSV, "csv", sniff_csv(ex.CSV))
    answers = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    report = etl_ui.run(spark, load)
    out = capsys.readouterr().out
    assert next(answers, None) is None                           # every answer used, none missing
    return LabSession.start(spark, load, report), out


def test_accepted_sale_value_column_answers_the_question(spark, monkeypatch, capsys):
    # rules: none (Enter) | sales_cop: yes (Enter) | transformations: apply | duplicates: remove
    session, out = real_etl(spark, monkeypatch, capsys, ["", "", "1", "1"])
    assert "sales_cop = round(quantity * unit_price_cop, 0)" in out
    df = session.df
    assert dict(df.dtypes)["sales_cop"] == "double"
    assert df.where(F.col("sales_cop") != F.round(F.col("quantity") * F.col("unit_price_cop"), 0)).count() == 0
    assert [r.text for r in session.etl.rules] == ["derivada: sales_cop = round(quantity * unit_price_cop, 0)"]
    assert session.etl.to_dict()["reglas"][0]["tipo"] == "columna derivada"     # in the ETL evidence
    expected = df.where("status = 'Entregado'").agg(
        F.avg(F.round(F.col("quantity") * F.col("unit_price_cop"), 0)), F.sum("sales_cop"),
        F.avg("unit_price_cop"), F.count(F.lit(1))).first()
    ev = session.ask(QUESTION, chooser({}))
    assert ev.sql == "SELECT AVG(sales_cop) AS avg_sales_cop\nFROM dataset\nWHERE status = 'Entregado'"
    assert ev.value == pytest.approx(expected[0])
    ev = session.ask("¿Cuál es la suma del valor de venta por pedido entregado?", chooser({}))
    assert "SUM(sales_cop)" in ev.sql and ev.value == pytest.approx(expected[1])
    ev = session.ask("¿Cuál es el promedio del precio unitario de pedidos entregados?", chooser({}))
    assert "AVG(unit_price_cop)" in ev.sql and ev.value == pytest.approx(expected[2])
    ev = session.ask("¿Cuántos pedidos entregados hay?", chooser({}))
    assert "COUNT(*)" in ev.sql and ev.value == expected[3]
    need = session.interpreter.interpret(parse_question("¿Cuál es el promedio del valor de pedidos entregados?"))
    assert need.key == "col:generic:valor"                                       # bare 'valor': still asked


def test_declined_column_is_never_invented(spark, monkeypatch, capsys):
    session, _ = real_etl(spark, monkeypatch, capsys, ["", "n", "1", "1"])
    assert "sales_cop" not in session.df.columns and session.etl.rules == []
    need = session.interpreter.interpret(parse_question(QUESTION))
    assert isinstance(need, NeedsInput) and "No se encontro una columna para 'valor de venta'" in need.message
    assert "sales_cop" not in [value for _, value in need.options]


def test_declared_derived_rules_are_not_overridden(spark, monkeypatch, capsys):
    rule = "derivada: venta_con_descuento = round(quantity * unit_price_cop * (1 - discount), 0)"
    session, out = real_etl(spark, monkeypatch, capsys, [rule, "", "1", "1"])
    assert "Formula de referencia" not in out                                     # nothing offered
    assert "venta_con_descuento" in session.df.columns and "sales_cop" not in session.df.columns


@pytest.mark.parametrize("columns, expected", [
    ([("quantity", "double"), ("unit_price_cop", "double"), ("status", "string")],
     "derivada: sales_cop = round(quantity * unit_price_cop, 0)"),
    ([("cantidad", "string"), ("precio_unitario", "string")],            # numbers written as text
     "derivada: sales_value = round(cantidad * precio_unitario, 0)"),
    ([("Date", "string"), ("Close", "double"), ("Volume", "bigint")], None),           # no quantity
    ([("quantity", "int"), ("status", "string")], None),                                # no price
    ([("quantity", "int"), ("unit_price", "double"), ("importe", "double")], None),     # amount exists
    ([("quantity", "int"), ("unit_price_cop", "double"), ("sales_cop", "double")], None),   # already there
])
def test_offer_only_when_quantity_and_price_without_amount(columns, expected):
    rule = sale_value_rule(columns)
    assert (rule.text if rule else None) == expected


def test_registered_dataset_gets_it_and_other_datasets_are_untouched(spark, monkeypatch, capsys):
    from app.ui import datasets as datasets_ui
    catalog = DatasetCatalog()
    script = [str(ex.CSV), "", "ventas", "s", "", "", "1", "1",        # path, CSV ok, alias, ETL: rules, sales_cop, ...
              str(DATA / "multi" / "clientes.csv"), "", "clientes", "n"]
    answers = iter(script)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    try:
        assert len(datasets_ui.register_many(spark, catalog, count=2)) == 2
        assert next(answers, None) is None
        assert "sales_cop" in catalog.get("ventas").df.columns
        assert catalog.get("clientes").df.columns == ["cliente_id", "nombre"]
        session = LabSession.from_catalog(spark, catalog, "ventas")
        ev = session.ask(QUESTION, chooser({}))
        assert "AVG(sales_cop)" in ev.sql and ev.tables_used == ["ventas"]      # automatic choice of dataset
        assert "sales_cop" in [r[0] for r in spark.sql("DESCRIBE ventas").collect()]   # also for manual SQL
    finally:
        for entry in catalog:
            catalog.remove(spark, entry.alias)
