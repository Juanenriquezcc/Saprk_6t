"""'valor de venta por pedido' is the sale amount (sales_cop), never the unit price.

Cause of the defect: 'valor' is a generic word (PRICE / AMOUNT / TOTAL); 'de venta' was not part of
any phrase, so only 'valor' was read and its choice, remembered for the session, decided every later
'valor ...' question silently (e.g. unit_price_cop chosen once for 'valor unitario').
Data: the real exam (tests/data/examen) after the analyzer's ETL with the reference rules, which
derive sales_cop = round(quantity * unit_price_cop, 0). Expected values: independent SQL over the
reference ETL of tests/exam_acceptance.py (same 11 758 rows, checked in test_exam_acceptance.py).
"""
import pytest

import exam_acceptance as ex
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from conftest import chooser


@pytest.fixture(scope="module")
def exam(spark):
    return ex.analyzer_etl(spark, ex.RULES + ex.EQUIVALENCES, confirm_equivalences=True)


@pytest.fixture(scope="module")
def expected(spark):
    ref = ex.reference_clean(spark)
    sql = ("SELECT AVG(sales_cop), SUM(sales_cop), AVG(unit_price_cop), COUNT(*) FROM ventas "
           "WHERE status = 'Entregado'")
    avg_sale, sum_sale, avg_price, orders = ex.run_reference(spark, ref, sql)[0]
    return {"avg_sale": avg_sale, "sum_sale": sum_sale, "avg_price": avg_price, "orders": orders}


def ask(session, text, answers=None):
    session.df.createOrReplaceTempView("dataset")
    return session.ask(text, chooser(answers or {}))


@pytest.mark.parametrize("question, sql, key", [
    ("¿Cuál es el promedio del valor de venta por pedido Entregado?", "AVG(sales_cop)", "avg_sale"),
    ("¿Cuál es el promedio del importe de venta por pedido entregado?", "AVG(sales_cop)", "avg_sale"),
    ("¿Cuál es la suma del valor de venta por pedido entregado?", "SUM(sales_cop)", "sum_sale"),
    ("¿Cuál es el promedio del precio unitario de pedidos entregados?", "AVG(unit_price_cop)", "avg_price"),
    ("¿Cuál es el promedio del valor unitario de pedidos entregados?", "AVG(unit_price_cop)", "avg_price"),
    ("¿Cuál es el promedio del precio de pedidos entregados?", "AVG(unit_price_cop)", "avg_price"),
    ("¿Cuántos pedidos entregados hay?", "COUNT(*)", "orders"),
])
def test_sale_value_vs_unit_price_vs_count(exam, expected, question, sql, key):
    exam.choices.clear()
    ev = ask(exam, question)                                   # chooser({}): nothing may be asked
    assert sql in ev.sql and "WHERE status = 'Entregado'" in ev.sql
    assert ev.value == pytest.approx(expected[key])


def test_a_choice_for_bare_valor_never_decides_the_sale_value(exam, expected):
    exam.choices.clear()
    need = exam.interpreter.interpret(parse_question("¿Cuál es el promedio del valor de pedidos entregados?"))
    assert need.key == "col:generic:valor"                     # bare 'valor' is still ambiguous: asked
    ask(exam, "¿Cuál es el promedio del valor de pedidos entregados?", {"col:generic:valor": "unit_price_cop"})
    ev = ask(exam, "¿Cuál es el promedio del valor de venta por pedido Entregado?")   # before: unit_price_cop
    assert "AVG(sales_cop)" in ev.sql and ev.value == pytest.approx(expected["avg_sale"])
    exam.choices.clear()


def test_without_sales_cop_the_measure_is_asked_never_invented(spark):
    rules = ex.RULES.replace("derivada: sales_cop = round(quantity * unit_price_cop, 0)\n", "")
    session = ex.analyzer_etl(spark, rules)
    assert "sales_cop" not in session.df.columns
    need = session.interpreter.interpret(parse_question("¿Cuál es el promedio del valor de venta por pedido Entregado?"))
    assert isinstance(need, NeedsInput) and "valor de venta" in need.message
    assert "No se encontro una columna" in need.message                      # explained, not substituted


def test_sale_value_in_a_multi_dataset_workshop(spark):
    from app.catalog import DatasetCatalog
    from app.session import LabSession
    catalog = DatasetCatalog()
    try:
        catalog.register(spark, "ventas", ex.CSV, etl=lambda load, view: __import__("app.etl.pipeline", fromlist=["x"])
                         .run(spark, load, __import__("app.etl.rules", fromlist=["x"]).parse_rules(ex.RULES),
                              lambda d: {"transform": "all", "unknown": True, "duplicates": True}[d.key], view=view))
        catalog.register(spark, "clientes", ex.DATA.parent / "multi" / "clientes.csv")
        session = LabSession.from_catalog(spark, catalog, "ventas")
        assert session.uses_catalog
        ev = session.ask("¿Cuál es el promedio del valor de venta por pedido Entregado?", chooser({}))
        assert "AVG(sales_cop)" in ev.sql and "FROM ventas" in ev.sql and ev.tables_used == ["ventas"]
    finally:
        for entry in catalog:
            catalog.remove(spark, entry.alias)
