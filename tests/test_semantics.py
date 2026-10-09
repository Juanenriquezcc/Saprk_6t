"""Phase 4: semantic hardening (identifiers, rows vs totals, max/min per group, filters with JOINs).

Single table: tests/data/joins/pedidos.csv as the active dataset (7 rows), by hand:
  pedido cliente producto unidades importe
  101    1       Laptop   1        3000
  102    1       Mouse    3        150
  103    2       Laptop   2        6000
  104    3       Mouse    1        50
  105    3       Teclado  2        200
  106    3       Mouse    2        100
  107    9       Teclado  1        100
  rows 7; units 12; units > 1: 102,103,105,106 -> 4; per client units 1:4, 2:2, 3:5, 9:1; orders 1:2, 2:1, 3:3, 9:1
  amount per product: max Laptop 6000, Teclado 200, Mouse 150; min Laptop 3000, Teclado 100, Mouse 50;
  avg Laptop 4500, Teclado 150, Mouse 100; units per product Mouse 6, Laptop 3, Teclado 3
Catalog: the fixtures of test_joins.py (clientes 1..5, pedido 107 has no client).
"""
import json

from app.export import export_workshop
from app.query import spec as s
from app.query.builder import build_sql
from app.query.spec import Condition, QuerySpec
from app.questions import validator as v
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from conftest import chooser
from test_joins import CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS, ask, confirm, refused, rows, shop, spec_of  # noqa: F401

ORDERS = "joins/pedidos.csv"


def interpret(session, text, answers=None):
    return session.interpreter.interpret(parse_question(text), answers or {})


def summary(spec):
    return (spec.intent, spec.shape, spec.aggregation, spec.target.columns if spec.target else None, spec.group_by)


# --- rows vs totals (single table) ----------------------------------------------------------

def test_number_of_orders_vs_sum_of_units(lab):
    session = lab(ORDERS)
    assert summary(interpret(session, "¿Cuántos pedidos hay?")) == (s.COUNT_ROWS, s.SCALAR, "COUNT", None, None)
    units = interpret(session, "¿Cuántas unidades se vendieron?")             # before: COUNT(*) = 7
    assert summary(units) == (s.AGG_SCALAR, s.SCALAR, "SUM", ("unidades",), None)
    assert ask(session, "¿Cuántas unidades se vendieron?").value == 12
    assert ask(session, "¿Cuántos pedidos hay?").value == 7
    over = ask(session, "¿Cuántos pedidos tienen unidades mayores a 1?")       # before: the filter was lost (7)
    assert over.filters == ["unidades > 1"] and over.value == 4 and "WHERE unidades > 1" in over.sql
    assert ask(session, "¿Cuántos pedidos tienen unidades menores o iguales a 1?").value == 3


def test_condition_not_understood_is_never_dropped(lab):
    need = interpret(lab(ORDERS), "¿Cuántos pedidos tienen unidades?")
    assert isinstance(need, NeedsInput) and need.key == "unrecognized"
    assert "la condicion sobre 'unidades'" in need.missing[0]


# --- extreme row vs aggregated group ----------------------------------------------------------

def test_extreme_row_vs_group_total(lab):
    session = lab(ORDERS)
    for question, value_col, pedido in (("¿Cuál es el pedido con mayor cantidad de unidades?", "unidades", 102),
                                        ("¿Cuál es el pedido con más unidades?", "unidades", 102),
                                        ("¿Qué pedido tiene el mayor importe?", "importe", 103)):
        spec = interpret(session, question)                                    # before: MAX value / a menu
        assert (spec.intent, spec.shape, spec.answer) == (s.RECORD_EXTREME, s.RECORD, "row"), question
        ev = ask(session, question)
        assert ev.result_rows[0][0] == pedido and f"ORDER BY {value_col} DESC" in ev.sql
    assert session.solve_question("¿Qué pedido tiene el mayor importe?", chooser({}), selected="103").validation == v.CORRECT
    top = ask(session, "¿Qué producto vendió más unidades en total?")
    assert (top.intent, top.value, top.result_rows[0][1]) == (s.GROUP_TOP, "Mouse", 6)


def test_max_and_min_per_group(lab):
    session = lab(ORDERS)
    for question, agg, expected in (("¿Cuál es el importe máximo por producto?", "MAX", {"Laptop": 6000, "Teclado": 200, "Mouse": 150}),
                                    ("¿Cuál es el importe mínimo por producto?", "MIN", {"Laptop": 3000, "Teclado": 100, "Mouse": 50}),
                                    ("¿Cuál es el importe promedio por producto?", "AVG", {"Laptop": 4500, "Teclado": 150, "Mouse": 100})):
        spec = interpret(session, question)                                    # before (MAX/MIN): SUM/AVG/record menu
        assert summary(spec) == (s.GROUP_AGG, s.GROUP, agg, ("importe",), "producto"), question
        assert rows(ask(session, question)) == expected
    high = ask(session, "¿Cuál es el producto con el importe promedio más alto?")
    low = ask(session, "¿Cuál es el producto con el importe promedio más bajo?")
    assert (high.intent, high.value, low.value) == (s.GROUP_TOP, "Laptop", "Mouse")
    # Still ambiguous (total, average or one order?): asked, never chosen.
    need = interpret(session, "¿Cuál es el producto con mayor importe?")
    assert need.key == "agg" and [value for _, value in need.options] == ["SUM", "AVG", "RECORD"]


# --- numeric identifiers ------------------------------------------------------------------------

def test_numeric_identifiers_group_and_count_but_are_not_summed(lab):
    session = lab(ORDERS)
    spec = interpret(session, "¿Cuál es el total de unidades por cliente_id?")  # before: asked if cliente_id was the measure
    assert summary(spec) == (s.GROUP_AGG, s.GROUP, "SUM", ("unidades",), "cliente_id")
    assert rows(ask(session, "¿Cuál es el total de unidades por cliente_id?")) == {3: 5, 1: 4, 2: 2, 9: 1}
    top = ask(session, "¿Qué cliente_id tiene más pedidos?")                  # before: COUNT(*) = 7, grouping lost
    assert (top.intent, top.value, top.result_rows[0][1]) == (s.GROUP_TOP, 3, 3)
    assert ask(session, "¿Cuántos cliente_id distintos hay?").value == 4
    need = interpret(session, "¿Cuál es la suma de pedido_id?")               # before: SUM(pedido_id) = 728
    assert need.key == "col:metric" and "'pedido id' es un identificador" in need.message
    assert [value for _, value in need.options] == ["unidades", "importe"]   # identifiers are not offered
    need = interpret(session, "¿Cuántos clientes distintos hay?")             # before: COUNT(*) = 7
    assert need.key == "col:distinct" and "cliente_id" in [value for _, value in need.options]
    assert ask(session, "¿Cuántos clientes distintos hay?", {"col:distinct": "cliente_id"}).value == 4


def test_single_table_sql_is_unchanged():
    spec = QuerySpec(s.COUNT_WHERE, s.SCALAR, "COUNT", filters=[Condition("Volume", ">", 2000)])
    assert build_sql(spec).sql == "SELECT COUNT(*) AS total_registros\nFROM dataset\nWHERE Volume > 2000"


# --- filters with JOINs --------------------------------------------------------------------------

def test_filters_of_a_left_join_stay_in_its_on(shop):
    confirm(shop, CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS)
    pick = {"table:producto": "pedidos"}
    question = "¿Cuántos pedidos de Laptop hizo cada cliente, incluyendo los que no tienen pedidos?"
    ev = ask(shop, question, pick)          # before: WHERE dropped Marta, Pedro and Sofia
    assert "LEFT JOIN pedidos ON clientes.cliente_id = pedidos.cliente_id AND pedidos.producto = 'Laptop'" in ev.sql
    assert "WHERE" not in ev.sql
    assert rows(ev) == {"Ana": 1, "Luis": 1, "Marta": 0, "Pedro": 0, "Sofia": 0}
    ev = ask(shop, "¿Cuántos pedidos con unidades mayores a 1 hizo cada cliente, incluyendo los que no tienen pedidos?")
    assert rows(ev) == {"Marta": 2, "Ana": 1, "Luis": 1, "Pedro": 0, "Sofia": 0}


def test_filters_of_entities_without_related_rows(shop):
    confirm(shop, CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS)
    pick = {"table:producto": "pedidos"}
    ev = ask(shop, "¿Qué clientes sin pedidos tienen cupo mayor a 600?")     # before: cupo ignored (Pedro too)
    assert [r[1] for r in ev.result_rows] == ["Sofia"] and "WHERE clientes.cupo > 600" in ev.sql
    ev = ask(shop, "¿Cuántos clientes no tienen pedidos de Laptop?", pick)   # before: 'de Laptop' ignored (2)
    assert ev.value == 3 and "LEFT ANTI JOIN pedidos ON clientes.cliente_id = pedidos.cliente_id AND pedidos.producto = 'Laptop'" in ev.sql
    ev = ask(shop, "¿Qué clientes de Bogota no tienen pedidos de Laptop?", pick)
    assert [r[1] for r in ev.result_rows] == ["Marta"]                        # Ana (Bogota) bought a Laptop
    assert ask(shop, "¿Qué clientes de Bogota no tienen pedidos?").result_rows == []   # Ana and Marta have orders
    refused(spec_of(shop, "¿Qué clientes sin pedidos tienen cupo?"), "No se entendio la condicion sobre 'cupo'")
    refused(spec_of(shop, "¿Qué clientes sin pedidos son de categoria Computo?"), "que no participa")


def test_inner_join_filters_and_orphan_keys(shop):
    confirm(shop, CLIENTES_PEDIDOS)
    ev = ask(shop, "¿Cuál es el total de importe por ciudad con unidades mayores a 1?")
    assert "WHERE pedidos.unidades > 1" in ev.sql and rows(ev) == {"Medellin": 6000, "Bogota": 150 + 200 + 100}
    assert any("1 registro(s) de pedidos sin correspondencia en clientes" in w for w in ev.warnings)   # pedido 107
    assert rows(ask(shop, "¿Cuántas unidades compró cada cliente?")) == {"Marta": 5, "Ana": 4, "Luis": 2}


def test_export_keeps_join_filters_and_warnings(shop, spark, tmp_path):
    confirm(shop, CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS)
    shop.history.clear()
    shop.solve_question("¿Cuántos pedidos de Laptop hizo cada cliente, incluyendo los que no tienen pedidos?",
                        chooser({"table:producto": "pedidos"}))
    shop.solve_question("¿Cuál es el total de unidades por ciudad?", chooser({}))
    folder = export_workshop(shop, tmp_path)
    sql = (folder / "taller.sql").read_text(encoding="utf-8-sig")
    assert "AND pedidos.producto = 'Laptop'" in sql
    for statement in [st for st in ("\n".join(l for l in x.splitlines() if not l.startswith("--")).strip()
                                    for x in sql.split(";")) if st]:
        spark.sql(statement).collect()
    data = json.loads((folder / "evidencia.json").read_text(encoding="utf-8"))
    assert any("INNER JOIN: 1 registro(s) de pedidos" in w for w in data["preguntas"][1]["warnings"])
    assert "Advertencia: INNER JOIN" in (folder / "evidencia.txt").read_text(encoding="utf-8-sig")
