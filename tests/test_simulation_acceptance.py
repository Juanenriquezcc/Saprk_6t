"""Phase 5.2: acceptance with four synthetic exercises (tests/data/simulation_a..d).

Every question goes through the real analyzer (parser -> QuerySpec -> builder -> executor -> evidence,
catalog, relations, ETL, menu). Expected values are computed by hand from the small files (written
below) or with Spark SQL written HERE, never with the analyzer's parser or builder.
Known defects are kept as xfail(strict=True) with their id (see tests/ACEPTACION_SIMULACION.md):
the test states the correct behavior and turns red the day it is fixed, so it cannot hide.

A  simulation_a/ventas.csv (8 orders): delivered O1 Laptop 2, O2 Laptop 1, O3 Mouse 5, O5 Teclado 4,
   O7 Mouse 2 -> 14 units, 5 orders; Laptop 3, Mouse 7, Teclado 4; Pasto 7, Cali 4, Bogota 3.
   All states: Laptop 2+1+3 = 6, Mouse 5+3+2 = 10, Teclado 4+1 = 5. Max unit_price 100 (3 Laptop rows).
B  simulation_b: clientes C1 Ana, C2 Luis, C3 Marta, C4 Sofia; pedidos P1 C1 100 Entregado, P2 C1 50
   Entregado, P3 C2 200 Pendiente, P4 C9 80 Entregado (C9 does not exist). Total 430, 4 orders.
   Join keeps P1, P2, P3 (350): Ana 150, Luis 200. Without orders: Marta, Sofia. Delivered: Ana 150.
C  simulation_c/ventas_sucias.csv (14 rows): row 3 '  laptop pro 14 ' (spaces, case), row 7
   'teclado mecanico' (case, accents), row 6 twice (exact duplicate), 8 empty quantity, 9 quantity -1,
   10 quantity 'abc', 11 price -5, rows 2/5 'Laptop Pro14'/'MouseX' (inner spaces), codes R001 / R-001.
   Rules: cantidad and precio required, > 0. Valid 9 = 14 - 4 rejected (8, 9, 10, 11) - 1 duplicate;
   valid units 2+1+1+3+2+1+2+2+1 = 15.
D  simulation_d/inventario.csv (11 products): max price 3000 twice (101 Laptop, 102 Desktop); max stock
   Mouse 50; stock NULL (105), 0 (104); price NULL (107), 0 (106); invalid date (108); stock by category
   Accesorios 50+0+30+10+4 = 94, Audio 8+12 = 20, Computo 5+3+7 = 15, Pantallas NULL; stock > 0: 9 rows.
"""
import os

import pytest

from app.errors import AppError
from app.etl import pipeline
from app.etl.rules import parse_rules
from app.questions.intents import NeedsInput
from app.session import CATALOG_SCOPE, DATASET_SCOPE
from app.spark.loader import load_dataset, resolve_path, sniff_csv
from conftest import DATA, chooser


def ask(session, text, answers=None):
    return session.ask(text, chooser(answers or {}))


def interpret(session, text, answers=None):
    from app.questions.parser import parse_question
    return session.active_interpreter.interpret(parse_question(text), answers or {})


def rows(ev):
    return {r[0]: r[1] for r in ev.result_rows}


def reference(spark, name, sql):
    """Independent reference: the raw CSV read by Spark itself and a SQL query written in the test."""
    spark.read.option("header", True).option("inferSchema", True).csv(str(DATA / name)).createOrReplaceTempView("ref")
    return [tuple(r) for r in spark.sql(sql).limit(100).collect()]


# --- A: sales and aggregations ------------------------------------------------------------------

A = "simulation_a/ventas.csv"


def test_a_rows_units_and_orders(lab, spark):
    s = lab(A)
    assert ask(s, "¿Cuántas filas tiene el dataset?").value == 8
    units = ask(s, "¿Cuántas unidades se vendieron en pedidos entregados?")
    assert (units.value, units.spec["aggregation"]) == (14, "SUM") and "SUM(quantity)" in units.sql
    assert "WHERE status = 'Entregado'" in units.sql
    orders = ask(s, "¿Cuántos pedidos entregados hay?")
    assert (orders.value, orders.spec["aggregation"]) == (5, "COUNT") and "WHERE status = 'Entregado'" in orders.sql
    assert reference(spark, A, "SELECT SUM(quantity), COUNT(*) FROM ref WHERE status = 'Entregado'") == [(14, 5)]


def test_a_groups(lab, spark):
    s = lab(A)
    top = ask(s, "¿Qué producto tuvo la mayor cantidad de unidades vendidas, considerando únicamente pedidos entregados?")
    assert (top.value, top.result_rows[0][1], top.spec["aggregation"]) == ("Mouse", 7, "SUM")
    by_product = ask(s, "¿Cuántas unidades se vendieron por producto en pedidos entregados?")
    assert rows(by_product) == {"Laptop": 3, "Mouse": 7, "Teclado": 4}
    assert rows(by_product) == dict(reference(spark, A, "SELECT product, SUM(quantity) FROM ref WHERE status = 'Entregado' GROUP BY product"))
    by_city = ask(s, "¿Cuál es la suma de unidades entregadas por ciudad?")
    assert rows(by_city) == {"Pasto": 7, "Cali": 4, "Bogota": 3} and "WHERE status = 'Entregado'" in by_city.sql
    every = ask(s, "¿Cuántas unidades se vendieron por producto, incluyendo todos los estados?")
    assert rows(every) == {"Laptop": 6, "Mouse": 10, "Teclado": 5} and "WHERE" not in every.sql


def test_a_max_price_and_its_product(lab):
    s = lab(A)
    ev = ask(s, "¿Cuál es el precio unitario máximo?")
    assert (ev.value, ev.spec["aggregation"]) == (100, "MAX") and "SUM" not in ev.sql
    # 'producto con el precio maximo' is asked (SUM / AVG / one record): see F8 (low).
    need = interpret(s, "¿Qué producto tiene el precio unitario máximo?")
    assert need.key == "agg" and [v for _, v in need.options] == ["SUM", "AVG", "RECORD"]
    ev = ask(s, "¿Qué producto tiene el precio unitario máximo?", {"agg": "RECORD"})
    assert ev.value == "Laptop" and any("Empate: 3 registros" in w for w in ev.warnings)


# F1 corregido en la fase 5.3 (antes xfail).
def test_a_unknown_grouping_word_is_not_silently_dropped(lab):
    need = interpret(lab(A), "¿Cuál es la suma de unidades por vendedor?")     # there is no 'vendedor'
    assert isinstance(need, NeedsInput)                                         # today: SUM = 21, no grouping


# --- B: related customers and orders --------------------------------------------------------------

B = DATA / "simulation_b"


@pytest.fixture
def shop(lab, spark):
    session = lab("simulation_b/pedidos.csv")
    for name in ("clientes", "pedidos"):
        session.catalog.register(spark, name, B / f"{name}.csv")
    relation = session.relations.propose(session.catalog)[0]
    session.relations.measure(session.catalog, relation)
    session.scope = CATALOG_SCOPE
    session.relation = relation
    yield session
    session.scope = DATASET_SCOPE
    session.catalog_choices.clear()
    for entry in session.catalog:
        session.catalog.remove(spark, entry.alias)


def test_b_relation_is_measured_with_its_orphans(shop):
    r = shop.relation
    assert r.label() == "clientes.cliente_id = pedidos.cliente_id" and r.status == "PENDIENTE"
    m = r.metrics
    assert (m.cardinality, m.matched_keys, m.left.orphans, m.right.orphans) == ("1:N", 2, 2, 1)   # C3, C4 / P4
    assert any("1 registro(s) de pedidos sin correspondencia en clientes" in w for w in m.warnings())


def test_b_unconfirmed_or_rejected_relations_are_never_used(shop):
    for status in ("PENDIENTE", "RECHAZADA"):
        if status == "RECHAZADA":
            shop.relations.reject(shop.relation)
        need = interpret(shop, "¿Cuál es el importe total por nombre?")
        assert isinstance(need, NeedsInput) and need.options == [] and f"esta {status}" in need.message
    assert ask(shop, "¿Cuál es el importe total?").value == 430          # one table: no relation needed
    assert ask(shop, "¿Cuántos pedidos hay?").value == 4


def test_b_questions_with_the_confirmed_relation(shop, spark):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    by_name = ask(shop, "¿Cuál es el importe total por nombre?")
    assert rows(by_name) == {"Luis": 200, "Ana": 150}                       # P4 (C9) is not invented a client
    assert any("1 registro(s) de pedidos sin correspondencia" in w for w in by_name.warnings)
    spark.read.option("header", True).csv(str(B / "clientes.csv")).createOrReplaceTempView("ref_c")
    spark.read.option("header", True).option("inferSchema", True).csv(str(B / "pedidos.csv")).createOrReplaceTempView("ref_p")
    independent = {r[0]: r[1] for r in spark.sql("SELECT c.nombre, SUM(p.importe) FROM ref_p p JOIN ref_c c "
                                                 "ON p.cliente_id = c.cliente_id GROUP BY c.nombre").collect()}
    assert rows(by_name) == independent and sum(independent.values()) == 430 - 80        # not inflated
    left = ask(shop, "¿Cuántos pedidos hizo cada nombre, incluyendo los que no tienen pedidos?")
    assert rows(left) == {"Ana": 2, "Luis": 1, "Marta": 0, "Sofia": 0} and "LEFT JOIN" in left.sql
    lonely = ask(shop, "¿Qué clientes no tienen pedidos?")
    assert sorted(r[1] for r in lonely.result_rows) == ["Marta", "Sofia"]
    delivered = ask(shop, "¿Cuál es el importe total por nombre en pedidos entregados?")
    assert rows(delivered) == {"Ana": 150} and "WHERE pedidos.estado = 'Entregado'" in delivered.sql
    assert rows(ask(shop, "¿Cuántos pedidos entregados hizo cada nombre?")) == {"Ana": 2}
    assert ask(shop, "¿Cuál es el importe total de Ana?").value == 150


def test_b_listing_joined_rows_needs_manual_sql(shop):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    assert isinstance(interpret(shop, "¿Qué pedidos entregados tienen cliente?"), NeedsInput)   # F5: not implemented
    ev = shop.run_sql("SELECT p.pedido_id, c.nombre FROM pedidos p LEFT JOIN clientes c ON p.cliente_id = c.cliente_id "
                      "WHERE p.estado = 'Entregado' ORDER BY p.pedido_id")
    assert ev.result_rows == [["P1", "Ana"], ["P2", "Ana"], ["P4", None]]          # P4: no client invented


# F1 corregido en la fase 5.3 (antes xfail).
def test_b_grouping_by_a_table_name_is_not_silently_dropped(shop):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    result = interpret(shop, "¿Cuál es el importe total por cliente?")
    assert isinstance(result, NeedsInput) or result.group_by is not None


# F1 corregido en la fase 5.3 (antes xfail).
def test_b_counting_per_table_name_is_not_silently_dropped(shop):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    result = interpret(shop, "¿Cuántos pedidos hizo cada cliente?")
    assert isinstance(result, NeedsInput) or result.group_by is not None


# F4 corregido en la fase 5.4 (antes xfail).
def test_b_total_amount_of_delivered_orders_by_name(shop):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    assert rows(ask(shop, "¿Cuál es el importe total de pedidos entregados por nombre?")) == {"Ana": 150}


MENU = ["11", str(B / "clientes.csv"), "", "n", "",            # register, suggested alias, no ETL
        "11", str(B / "pedidos.csv"), "", "n", "",
        "13", "1", "1", "2", "", "0",                           # relations: review #1 -> reject
        "14", "",                                               # questions on the catalog
        "2", "¿Cuál es el importe total por nombre?", "", "",   # refused: relation rejected
        "13", "1", "1", "1", "s", "", "0",                      # review again -> confirm, accept warnings
        "2", "¿Cuál es el importe total por nombre?", "", "",   # answered with the JOIN
        "0"]


def test_b_interactive_menu_flow(lab, spark, monkeypatch, capsys):
    from app.ui import menu
    session = lab("simulation_a/ventas.csv")
    answers = iter(MENU)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    try:
        menu.run(session)
        out = capsys.readouterr().out
        assert next(answers, None) is None and "Traceback" not in out
        assert "esta RECHAZADA" in out and "Relacion clientes.cliente_id = pedidos.cliente_id: CONFIRMADA" in out
        assert "Luis   | 200" in out and "Ana    | 150" in out
    finally:
        session.scope = DATASET_SCOPE
        session.catalog_choices.clear()
        for entry in session.catalog:
            session.catalog.remove(spark, entry.alias)


# --- C: data quality and equivalences ---------------------------------------------------------------

C = "simulation_c/ventas_sucias.csv"
C_RULES = "requerido: cantidad, precio\ncantidad > 0\nprecio > 0\n"


def run_c(spark, extra="", equivalence=None):
    path = resolve_path(str(DATA / C))
    load = load_dataset(spark, path, "csv", sniff_csv(path))
    asked = []

    def decide(d):
        asked.append(d.key)
        if d.key.startswith("equivalence:"):
            return equivalence
        return {"transform": "all", "unknown": True, "duplicates": True}[d.key]
    report = pipeline.run(spark, load, parse_rules(C_RULES + extra), decide)
    return load, report, asked


def clean_rows(report):
    return {r[0]: tuple(r[1:]) for r in report.df.select("fila", "referencia", "producto", "cantidad", "precio").collect()}


def test_c_load_and_quality(spark):
    load, report, asked = run_c(spark)
    assert load.rows == 14 and dict(load.df.dtypes)["cantidad"] == "string"       # 'abc' kept it as text
    assert (report.original_rows, report.valid_rows, report.rejected_rows, report.duplicates_removed) == (14, 9, 4, 1)
    rejected = {r[0]: r[1] for r in spark.table("rechazados").select("fila", "motivo_rechazo").collect()}
    assert sorted(rejected) == [8, 9, 10, 11]
    assert "cantidad es requerido" in rejected[8] and "valor no valido en cantidad" in rejected[10]
    assert rejected[9] == "regla: cantidad > 0" and rejected[11] == "regla: precio > 0"
    valid = clean_rows(report)
    assert sorted(valid) == [1, 2, 3, 4, 5, 6, 7, 12, 13] and sum(v[2] for v in valid.values()) == 15
    assert valid[3][1] == "Laptop Pro 14" and valid[7][1] == "Teclado Mecánico"      # spaces/case/accents unified
    assert not any(k.startswith("equivalence:") for k in asked)


def test_c_similar_values_are_detected_not_applied(spark):
    _, report, _ = run_c(spark)
    assert report.similar_values == ["referencia: 'R-001' (1) / 'R001' (1)",
                                     "producto: 'Laptop Pro 14' (2) / 'Laptop Pro14' (1)",
                                     "producto: 'Mouse X' (1) / 'MouseX' (1)"]
    assert not any(t.kind == "equivalence" for t in report.transformations)       # detection is not a change
    valid = clean_rows(report)
    assert (valid[2][1], valid[5][1], valid[13][0], valid[1][0]) == ("Laptop Pro14", "MouseX", "R001", "R-001")


def test_c_confirmed_equivalences_touch_only_their_values(spark):
    _, base, _ = run_c(spark)
    before = clean_rows(base)
    for old, row, new in (("Laptop Pro14", 2, "Laptop Pro 14"), ("MouseX", 5, "Mouse X")):
        _, report, asked = run_c(spark, f"equivalencia: producto: {old} -> {new}\n", equivalence=True)
        after = clean_rows(report)
        assert after[row][1] == new and sum(v[2] for v in after.values()) == 15
        assert {k: v for k, v in after.items() if k != row} == {k: v for k, v in before.items() if k != row}
        assert after[row][0] == before[row][0] and after[row][2:] == before[row][2:]     # other columns unchanged
        applied = [t.to_dict() for t in report.transformations if t.kind == "equivalence"]
        assert len(applied) == 1 and applied[0]["a"] == new
        assert f"equivalence:producto:{old}" in asked
        assert report.to_dict()["decisiones"][f"equivalence:producto:{old}"] is True      # the user's decision, recorded


def test_c_unconfirmed_equivalence_is_not_applied(spark):
    _, report, _ = run_c(spark, "equivalencia: producto: Laptop Pro14 -> Laptop Pro 14\n", equivalence=False)
    assert clean_rows(report)[2][1] == "Laptop Pro14"
    assert "Equivalencia no confirmada (no aplicada): producto: 'Laptop Pro14' -> 'Laptop Pro 14'." in report.warnings


def test_c_numeric_identifier_is_protected(spark):
    with pytest.raises(AppError, match="no es una columna de texto descriptivo"):
        run_c(spark, "equivalencia: fila: 2 -> 1\n", equivalence=True)


# F2 corregido en la fase 5.3 (antes xfail).
def test_c_code_column_is_protected(spark):
    with pytest.raises(AppError):
        run_c(spark, "equivalencia: referencia: R001 -> R-001\n", equivalence=True)


def test_c_repeating_the_etl_does_not_accumulate(spark):
    _, first, _ = run_c(spark, "equivalencia: producto: Laptop Pro14 -> Laptop Pro 14\n", equivalence=True)
    _, second, _ = run_c(spark, "equivalencia: producto: Laptop Pro14 -> Laptop Pro 14\n", equivalence=True)
    assert clean_rows(first) == clean_rows(second)
    assert [t.to_dict() for t in first.transformations] == [t.to_dict() for t in second.transformations]
    assert (first.valid_rows, first.rejected_rows) == (second.valid_rows, second.rejected_rows) == (9, 4)


# --- D: ambiguity, identifiers and edge cases ----------------------------------------------------------

D = "simulation_d/inventario.csv"


def test_d_max_price_and_ties(lab):
    s = lab(D)
    ev = ask(s, "¿Cuál es el precio máximo?")
    assert (ev.value, ev.spec["aggregation"]) == (3000, "MAX") and "SUM" not in ev.sql
    assert interpret(s, "¿Qué producto tiene el precio máximo?").key == "agg"       # asked, not guessed
    ev = ask(s, "¿Qué producto tiene el precio máximo?", {"agg": "RECORD"})
    assert ev.value in ("Laptop", "Desktop") and any("Empate: 2 registros" in w for w in ev.warnings)   # tie reported


def test_d_stock(lab, spark):
    s = lab(D)
    top = ask(s, "¿Qué categoría tiene el mayor stock total?")
    assert (top.value, top.result_rows[0][1]) == ("Accesorios", 94)
    by_cat = ask(s, "¿Cuál es el stock total por categoría?")
    assert rows(by_cat) == {"Accesorios": 94, "Audio": 20, "Computo": 15, "Pantallas": None}   # NULL is not 0
    assert rows(by_cat) == dict(reference(spark, D, "SELECT categoria, SUM(stock) FROM ref GROUP BY categoria"))
    most = ask(s, "¿Qué producto tiene el mayor stock?", {"agg": "RECORD"})
    assert most.value == "Mouse" and most.result_rows[0][3] == 50                    # not the max-price product
    assert ask(s, "¿Cuántos productos tienen stock mayor que 0?").value == 9


def test_d_identifiers_dates_and_counts(lab):
    s = lab(D)
    for question in ("¿Cuál es la suma de producto_id?", "¿Cuál es el total de proveedor_id?"):
        need = interpret(s, question)
        assert need.key == "col:metric" and [v for _, v in need.options] == ["stock", "precio"]   # never an id
    assert ask(s, "¿Cuántos proveedor_id distintos hay?").value == 4
    assert ask(s, "¿Cuántos productos distintos hay?").value == 11          # the column named 'producto'
    assert ask(s, "¿Cuántos productos se actualizaron en marzo de 2025?").value == 2              # 103, 104
    assert ask(s, "¿Cuántos productos se actualizaron después del 2025-06-01?").value == 3       # 109, 110, 111
    # 'Stock' is a product and a column: a clarification, never a silent answer.
    assert isinstance(interpret(s, "¿Cuál es el precio de Stock?"), NeedsInput)
    # 'precio nulo' is not supported (F6): refused, not answered with a wrong number.
    assert isinstance(interpret(s, "¿Cuántos registros tienen precio nulo?"), NeedsInput)


# F3 corregido en la fase 5.3 (antes xfail).
def test_d_number_word_condition_is_not_dropped(lab):
    result = interpret(lab(D), "¿Cuántos productos tienen stock mayor que cero?")
    assert isinstance(result, NeedsInput) or [f.label() for f in result.filters] == ["stock > 0"]


# F7 corregido en la fase 5.4 (antes xfail).
def test_d_total_of_filtered_products(lab):
    assert ask(lab(D), "¿Cuál es el stock total de los productos con precio mayor a 100?").value == 5 + 3 + 8 + 7
