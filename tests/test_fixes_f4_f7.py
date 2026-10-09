"""Phase 5.4: F4 ('importe total de pedidos' is a SUM) and F7 ('los productos con <condición>' is a filter).

Expected values by hand (data of tests/test_simulation_acceptance.py):
  A ventas, delivered: unit_price 100 + 100 + 20 + 50 + 20 = 290; quantity 2 + 1 + 5 + 4 + 2 = 14; 5 orders.
  B per name, delivered: amount Ana 100 + 50 = 150 (P4 has no client), orders Ana 2.
  D inventario: price > 100 -> Laptop 5, Desktop 3, Monitor NULL, Parlante 8, Tablet 7 -> SUM(stock) 23,
    5 products; stock > 5 -> prices 20, 0, NULL, 150, 90, 1200 -> AVG 1460 / 5 = 292.
"""
import pytest

from test_simulation_acceptance import A, D, ask, rows, shop  # noqa: F401


# --- F4: a measure before 'total de <rows>' is summed; the rows alone are counted --------------

@pytest.mark.parametrize("question, sql, value", [
    ("¿Cuál es el precio total de pedidos entregados?", "SUM(unit_price)", 290),        # a measure -> SUM
    ("¿Cuál es el total de pedidos entregados?", "COUNT(*)", 5),                        # the rows -> COUNT
    ("¿Cuál es el número de pedidos entregados?", "COUNT(*)", 5),
    ("¿Cuántos pedidos entregados hay?", "COUNT(*)", 5),
])
def test_f4_measure_total_vs_count_single_table(lab, question, sql, value):
    ev = ask(lab(A), question)
    assert sql in ev.sql and "WHERE status = 'Entregado'" in ev.sql and ev.value == value


def test_f4_catalog_amount_vs_count_by_name(shop):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    amount = ask(shop, "¿Cuál es el importe total de pedidos entregados por nombre?")    # before: refused
    assert amount.spec["aggregation"] == "SUM" and amount.spec["target"]["columns"] == ["pedidos.importe"]
    assert amount.spec["group_by"] == "clientes.nombre" and "WHERE pedidos.estado = 'Entregado'" in amount.sql
    assert rows(amount) == {"Ana": 150}
    for question in ("¿Cuál es el total de pedidos entregados por nombre?", "¿Cuál es el número de pedidos entregados por nombre?"):
        count = ask(shop, question)
        assert count.spec["aggregation"] == "COUNT" and rows(count) == {"Ana": 2}, question


# --- F7: 'los productos con <condición>' filters; 'por producto' / a superlative still group -------

def test_f7_filtered_total_is_one_value(lab):
    s = lab(D)
    ev = ask(s, "¿Cuál es el stock total de los productos con precio mayor a 100?")       # before: per product
    assert (ev.spec["shape"], ev.spec["group_by"]) == ("SCALAR", None) and "GROUP BY" not in ev.sql
    assert ev.sql == "SELECT SUM(stock) AS sum_stock\nFROM dataset\nWHERE precio > 100" and ev.value == 23
    ev = ask(s, "¿Cuál es el precio promedio de los productos con stock mayor a 5?")
    assert "GROUP BY" not in ev.sql and ev.value == pytest.approx(292)                     # NULL price not counted
    assert ask(s, "¿Cuántos productos con precio mayor a 100 hay?").value == 5


def test_f7_explicit_breakdown_and_ranking_still_group(lab):
    s = lab(D)
    ev = ask(s, "¿Cuál es el stock total por producto con precio mayor a 100?")
    assert rows(ev) == {"Parlante": 8, "Tablet": 7, "Laptop": 5, "Desktop": 3, "Monitor": None}
    assert "GROUP BY producto" in ev.sql and "WHERE precio > 100" in ev.sql
    top = ask(s, "¿Cuál es la categoría con el mayor stock total?")
    assert (top.spec["intent"], top.value) == ("GROUP_TOP", "Accesorios")
    assert s.interpreter.interpret(__import__("app.questions.parser", fromlist=["x"]).parse_question(
        "¿Qué producto tiene el mayor stock?")).key == "agg"                              # unchanged: asked
