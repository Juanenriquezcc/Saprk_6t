"""Question engine on data that went through the ETL (tests/data/ventas_sucio.csv).

Clean rows after the ETL (see test_etl.py), by hand:
  id city     category   qty unit_price  discount status
  1  Bogota   Tecnologia 2   2.500.000   0.10     Entregado   2025-01-15
  2  Bogota   Tecnologia 5      45.000   0        Entregado   2025-02-15
  3  Bogota   Hogar      1     350.000   0.05     Devuelto    2025-03-01
  4  Medellin Tecnologia 1   2.400.000   0        Entregado   2025-03-10
  5  Bogota   Ropa       3      80.000   0.20     Entregado   2025-03-20
  6  Cali     Hogar      2     350.000   0        Cancelado   2025-04-02
  7  Pasto    Ropa       4      75.000   0        Entregado   2025-04-05
  13 Bogota   Hogar      1     350.000   0        NULL        2025-06-12
"""
import pytest

from app.etl import pipeline
from app.etl.rules import parse_rules
from app.query import spec as s
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from app.session import LabSession
from app.spark.loader import load_dataset, resolve_path, sniff_csv
from conftest import DATA, chooser

RULES = "quantity entre 1 y 20\ncustomer_age BETWEEN 18 AND 100\nreturned_qty <= quantity\naño(order_date) = 2025"
REVENUE = "metric:ingresos"


@pytest.fixture(scope="module")
def session(spark):
    path = resolve_path(str(DATA / "ventas_sucio.csv"))
    load = load_dataset(spark, path, "csv", sniff_csv(path))
    answers = {"transform": "all", "unknown": True, "duplicates": True}
    etl = pipeline.run(spark, load, parse_rules(RULES), lambda d: answers[d.key])
    return LabSession.start(spark, load, etl)


@pytest.fixture
def lab_session(session):
    session.etl.df.createOrReplaceTempView("dataset")
    session.history.clear()
    session.choices.clear()
    return session


def ask(session, text, answers=None):
    return session.ask(text, chooser(answers or {}))


def test_session_profile_is_the_clean_data(lab_session):
    assert lab_session.profile.rows == 8
    assert lab_session.semantic.resolved("CITY") == "city"
    assert lab_session.semantic.resolved("STATUS") == "status"
    assert ask(lab_session, "¿Cuántos registros hay?").value == 8


def test_city_with_more_delivered_orders(lab_session):
    ev = ask(lab_session, "¿Cuál ciudad tiene más pedidos entregados?")
    assert ev.value == "Bogota" and ev.intent == s.GROUP_TOP
    assert "status = 'Entregado'" in ev.sql and "COUNT(*)" in ev.sql and "LIMIT 1" in ev.sql


def test_revenue_formula_is_asked_never_invented(lab_session):
    need = lab_session.interpreter.interpret(parse_question("¿Cuál categoría genera mayores ingresos?"))
    assert isinstance(need, NeedsInput) and need.key == REVENUE and need.message.startswith("METRICA AMBIGUA")
    labels = [label for label, _ in need.options]
    assert labels[0].startswith("quantity x unit_price_cop") and "(1 - discount)" in labels[1]

    plain, discounted = [v for _, v in need.options]
    ev = ask(lab_session, "¿Cuál categoría genera mayores ingresos?", {REVENUE: plain})
    assert ev.value == "Tecnologia" and "SUM((quantity * unit_price_cop))" in ev.sql
    assert ev.result_rows[0][1] == pytest.approx(7_625_000)
    # The choice is remembered for the session: no second question.
    ev = ask(lab_session, "¿Cuáles son los ingresos totales?")
    assert ev.value == pytest.approx(2*2.5e6 + 5*45e3 + 350e3 + 2.4e6 + 3*80e3 + 2*350e3 + 4*75e3 + 350e3)

    lab_session.choices.clear()
    ev = ask(lab_session, "¿Cuál categoría genera mayores ingresos?", {REVENUE: discounted})
    assert ev.result_rows[0][1] == pytest.approx(4.5e6 + 225e3 + 2.4e6)


def test_ventas_can_mean_number_of_sales(lab_session):
    assert ask(lab_session, "¿Cuántas ventas hubo?").value == 8          # counting: nothing to ask
    ev = ask(lab_session, "¿Qué ciudad genera más ventas?", {"metric:ventas": "COUNT"})
    assert ev.value == "Bogota" and ev.result_rows[0][1] == 5


def test_true_false_with_comparison(lab_session):
    ev = ask(lab_session, "¿El promedio de quantity es mayor a 2? V/F")      # 19 / 8 = 2.375
    assert ev.question_type == s.TRUE_FALSE and ev.claim_op == ">" and ev.verdict == "VERDADERO"
    assert ask(lab_session, "¿El promedio de quantity es mayor a 3? V/F").verdict == "FALSO"
    assert ask(lab_session, "El total de quantity es menor o igual a 19").verdict == "VERDADERO"


def test_comparison_between_two_groups(lab_session):
    ev = ask(lab_session, "¿Bogotá tiene más pedidos que Cali? (V/F)")
    assert ev.verdict == "VERDADERO" and "city IN ('Bogota', 'Cali')" in ev.sql
    ev = ask(lab_session, "¿Cuál tiene más pedidos, Cali o Pasto?")
    assert any("Empate" in w for w in ev.warnings)            # 1 and 1: reported, never hidden


def test_time_grouping_and_running_total(lab_session):
    ev = ask(lab_session, "¿En qué mes se vendieron más unidades?")
    assert ev.value == "2025-04" and "date_format(order_date, 'yyyy-MM')" in ev.sql
    ev = ask(lab_session, "Cantidad total por mes acumulada")
    assert ev.result_rows == [["2025-01", 2, 2], ["2025-02", 5, 7], ["2025-03", 5, 12], ["2025-04", 6, 18],
                              ["2025-06", 1, 19]]


def test_having(lab_session):
    ev = ask(lab_session, "¿Cuántas ciudades tienen al menos 2 pedidos?")
    assert ev.intent == s.GROUP_COUNT and ev.value == 1 and "HAVING total_registros >= 2" in ev.sql
    ev = ask(lab_session, "¿Qué ciudades tienen un promedio de quantity mayor a 2?")
    assert [r[0] for r in ev.result_rows] == ["Pasto", "Bogota"] and "HAVING avg_quantity > 2" in ev.sql


def test_median_and_between(lab_session):
    assert ask(lab_session, "¿Cuál es la mediana de quantity?").value == 2
    ev = ask(lab_session, "¿Cuántos registros tienen quantity entre 2 y 4?")
    assert ev.value == 4 and ev.filters == ["quantity >= 2", "quantity <= 4"]


# --- final audit: semantic decisions, explicit formulas, ETL traceability -------------------------

def test_semantic_decisions(lab_session):
    it = lab_session.interpreter
    spec = it.interpret(parse_question("¿Qué ciudad tiene más unidades vendidas?"))
    assert (spec.aggregation, spec.target.columns, spec.group_by) == ("SUM", ("quantity",), "city")
    spec = it.interpret(parse_question("¿Qué ciudad tiene el mayor número de pedidos?"))
    assert (spec.aggregation, spec.target, spec.group_by) == ("COUNT", None, "city")
    for q in ("¿Qué ciudad tuvo el mayor ingreso?", "¿Qué categoría tuvo mayores ingresos?"):
        need = it.interpret(parse_question(q))
        assert isinstance(need, NeedsInput) and need.key == REVENUE    # nothing runs before the choice
    assert lab_session.history == []


def test_explicit_formula_has_priority(lab_session):
    for q in ("¿Qué categoría tuvo mayores ingresos (quantity * unit_price_cop)?",
              "¿Qué categoría tuvo el mayor total de quantity * unit_price_cop?"):
        ev = ask(lab_session, q)                    # no METRICA AMBIGUA, no column question
        assert "SUM((quantity * unit_price_cop))" in ev.sql and ev.value == "Tecnologia"
    ev = ask(lab_session, "¿Qué ciudad tuvo más ventas (quantity x unit_price_cop)?")   # 'ventas' is only a name
    assert "SUM((quantity * unit_price_cop))" in ev.sql and ev.value == "Bogota"


def test_etl_traceability(lab_session, spark):
    category = [t.to_dict() for t in lab_session.etl.transformations if t.kind == "category"]
    city = next(t for t in category if t["columna"] == "city")
    assert city["mapeo"] == {"BOGOTA": "Bogota", "Bogotá": "Bogota", "Medellín": "Medellin"} and city["regla"]
    original = {r[0] for r in spark.sql("SELECT city FROM dataset_original").collect()}
    assert {"Bogotá", "BOGOTA ", " Cali"} <= original                # the original is never modified
    dup = spark.sql("SELECT order_id, COUNT(*) AS copias FROM dataset_original GROUP BY ALL HAVING COUNT(*) > 1")
    assert [tuple(r) for r in dup.select("order_id", "copias").collect()] == [(13, 2)]
    assert all(r[0] for r in spark.sql("SELECT motivo_rechazo FROM rechazados").collect())
    # Missing values stay missing: nothing is imputed.
    assert spark.sql("SELECT COUNT(*) FROM dataset WHERE status IS NULL").first()[0] == 1
