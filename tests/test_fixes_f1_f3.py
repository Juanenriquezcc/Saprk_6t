"""Phase 5.3: fixes F1 (grouping never dropped), F2 (no equivalences on codes), F3 ('mayor que cero').

Data of tests/test_simulation_acceptance.py (expected values by hand):
  A ventas: all states per city Pasto 2+5 = 7, Bogota 1+3+2 = 6, Cali 4+1+3 = 8; 21 units; 8 orders
     (Entregado 5, Pendiente 2, Devuelto 1).
  B clientes/pedidos: per name Luis 200, Ana 150 (P4 has no client); orders Ana 2, Luis 1.
  C ventas_sucias: 'referencia' holds codes (R-001 ... R-012, R001) and a repeated row (R-006).
  D inventario: stock 5, 3, 50, 0, NULL, 30, 10, 8, 12, 7, 4 -> > 0: 9 rows, = 0: 1, >= 0: 10 (NULL is
     never counted); price = 0: 1 row (Cable).
"""
import pytest

from app.errors import AppError
from app.questions.intents import NeedsInput
from conftest import chooser
from test_simulation_acceptance import A, D, ask, interpret, rows, run_c, shop  # noqa: F401


# --- F1: an explicit grouping is never dropped -----------------------------------------------------

def test_f1_existing_grouping_is_unchanged(lab):
    ev = ask(lab(A), "¿Cuál es la suma de unidades por ciudad?")          # chooser({}): nothing is asked
    assert rows(ev) == {"Cali": 8, "Pasto": 7, "Bogota": 6} and "GROUP BY city" in ev.sql


def test_f1_unknown_grouping_word_asks_for_a_real_column(lab):
    s = lab(A)
    need = interpret(s, "¿Cuál es la suma de unidades por vendedor?")      # before: SUM = 21, no grouping
    assert need.key == "group:vendedor" and "'vendedor'" in need.message
    assert [v for _, v in need.options] == ["order_id", "product", "status", "city"]   # real columns, no measure
    ev = ask(s, "¿Cuál es la suma de unidades por vendedor?", {"group:vendedor": "city"})
    assert rows(ev) == {"Cali": 8, "Pasto": 7, "Bogota": 6} and "GROUP BY city" in ev.sql
    ev = ask(s, "¿Cuántos pedidos hay por vendedor?", {"group:vendedor": "status"})
    assert rows(ev) == {"Entregado": 5, "Pendiente": 2, "Devuelto": 1}
    cancelled = s.solve_question("¿Cuál es la suma de unidades por vendedor?", lambda need: None)
    assert cancelled.validation == "NO RESUELTA" and cancelled.sql == ""      # never a global total


def test_f1_global_aggregates_are_unchanged(lab):
    s = lab(A)
    assert ask(s, "¿Cuál es la suma de unidades?").value == 21
    assert ask(s, "¿Cuál es el promedio de unidades por pedido?").value == pytest.approx(21 / 8)   # per row
    assert ask(s, "¿Cuántos pedidos entregados hay?").value == 5


def test_f1_table_name_in_catalog_offers_its_columns(shop):
    shop.relations.confirm(shop.relation, accept_warnings=True)
    need = interpret(shop, "¿Cuál es el importe total por cliente?")        # before: 350, no grouping
    assert need.key == "group:cliente" and [v for _, v in need.options] == ["cliente_id", "nombre"]
    ev = ask(shop, "¿Cuál es el importe total por cliente?", {"group:cliente": "nombre"})
    assert rows(ev) == {"Luis": 200, "Ana": 150} and "GROUP BY clientes.nombre" in ev.sql
    ev = ask(shop, "¿Cuántos pedidos hizo cada cliente?", {"group:cliente": "nombre"})   # before: 3
    assert rows(ev) == {"Ana": 2, "Luis": 1}
    # cliente_id exists in both tables: which one is asked too, never chosen.
    need = interpret(shop, "¿Cuál es el importe total por cliente?", {"group:cliente": "cliente_id"})
    assert need.key == "table:cliente_id"
    ev = ask(shop, "¿Cuál es el importe total por cliente?", {"group:cliente": "cliente_id", "table:cliente_id": "clientes"})
    assert rows(ev) == {"C2": 200, "C1": 150}


# --- F2: codes are never unified -------------------------------------------------------------------

def test_f2_exact_case_with_duplicates_is_refused_and_codes_are_kept(spark):
    _, plain, _ = run_c(spark)
    codes = [r[0] for r in plain.df.select("referencia").collect()]          # 9 valid rows
    assert "R001" in codes and "R-001" in codes                                # both codes kept
    raw = spark.read.option("header", True).csv(str(__import__("conftest").DATA / "simulation_c/ventas_sucias.csv"))
    distinct, total = raw.select("referencia").distinct().count(), raw.count()
    assert distinct / total < 0.95                                             # the duplicate lowers the ratio
    with pytest.raises(AppError, match="codigos o referencias"):              # before: applied (R001 -> R-001)
        run_c(spark, "equivalencia: referencia: R001 -> R-001\n", equivalence=True)
    with pytest.raises(AppError, match="codigos o referencias"):              # descriptive words, code column
        run_c(spark, "equivalencia: referencia: desconocido -> sin dato\n", equivalence=True)


def test_f2_code_shaped_values_are_refused_in_a_descriptive_column(spark):
    with pytest.raises(AppError, match="'MX-500' tiene forma de codigo"):
        run_c(spark, "equivalencia: producto: MX-500 -> Mouse X\n", equivalence=True)


def test_f2_descriptive_equivalences_still_work(spark):
    _, report, _ = run_c(spark, "equivalencia: producto: Laptop Pro14 -> Laptop Pro 14\n"
                                "equivalencia: producto: MouseX -> Mouse X\n", equivalence=True)
    names = {r[0]: r[1] for r in report.df.select("fila", "producto").collect()}
    assert (names[2], names[5]) == ("Laptop Pro 14", "Mouse X")
    assert [t.params["from"] for t in report.transformations if t.kind == "equivalence"] == ["Laptop Pro14", "MouseX"]


# --- F3: 'mayor que cero' --------------------------------------------------------------------------

@pytest.mark.parametrize("question, op, value, expected", [
    ("¿Cuántos productos tienen stock mayor que cero?", ">", 0, 9),           # before: 11, filter lost
    ("¿Cuántos productos tienen stock mayor que 0?", ">", 0, 9),
    ("¿Cuántos productos tienen stock igual a cero?", "=", 0, 1),             # 104
    ("¿Cuántos productos tienen stock mayor o igual a cero?", ">=", 0, 10),   # 105 (NULL) never counted
    ("¿Cuántos productos tienen precio igual a cero?", "=", 0, 1),            # 106 Cable
])
def test_f3_number_word_in_comparisons(lab, question, op, value, expected):
    ev = ask(lab(D), question)
    column = "precio" if "precio" in question else "stock"
    assert ev.spec["filters"] == [{"column": column, "op": op, "value": value, "other_column": None}]
    assert f"WHERE {column} {op} 0" in ev.sql and ev.value == expected


def test_f3_cero_outside_a_comparison_is_not_a_filter(lab):
    need = interpret(lab(D), "¿Cuántos productos tienen cero stock?")       # no operator: refused, never 11
    assert isinstance(need, NeedsInput) and "la condicion sobre 'stock'" in need.missing[0]
