"""Questions over several catalog tables: JOINs only through CONFIRMED relations (phase 3).

Synthetic fixtures in tests/data/joins, expected values counted by hand:
  clientes   1 Ana Bogota 1000 | 2 Luis Medellin 2000 | 3 Marta Bogota 1500 | 4 Pedro Cali 500 | 5 Sofia Pasto 800
  pedidos    101 c1 Laptop 1u 3000 | 102 c1 Mouse 3u 150 | 103 c2 Laptop 2u 6000 | 104 c3 Mouse 1u 50
             105 c3 Teclado 2u 200 | 106 c3 Mouse 2u 100 | 107 c9 Teclado 1u 100   (client 9 does not exist)
  productos  Laptop Computo | Mouse Accesorios | Teclado Accesorios | Monitor Computo (never ordered)
  perfiles   c1 Oro | c2 Plata | c3 Oro | c4 Bronce | c6 Plata          (clientes 1:1 perfiles)
  promociones Mouse Verano | Mouse Navidad | Laptop Verano             (pedidos N:M promociones)
  codigos    cliente_id 'C001', 'C002' (text: incompatible with clientes.cliente_id)

  INNER clientes-pedidos drops pedido 107: orders per client Ana 2, Luis 1, Marta 3; amount Ana 3150,
  Luis 6000, Marta 350; units per city Bogota 1+3+1+2+2 = 9, Medellin 2.
  Clients without orders: Pedro, Sofia. Products without orders: Monitor.
  Units per category in Bogota: Computo 1 (101), Accesorios 3+1+2+2 = 8.
  Average cupo per segment (1:1, Sofia and c6 unmatched): Oro (1000+1500)/2 = 1250, Plata 2000, Bronce 500.
  Units per product: Laptop 1+2 = 3, Mouse 3+1+2 = 6, Teclado 2+1 = 3.   Per segment: Oro 4+5 = 9, Plata 2.
"""
import json

import pytest

from app.export import export_workshop
from app.query import spec as s
from app.query.builder import build_sql, from_sql, ident, output_names
from app.query.spec import Join, Metric, QuerySpec
from app.questions import validator as v
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from app.session import CATALOG_SCOPE, DATASET_SCOPE
from app.spark.loader import CsvOptions
from conftest import DATA, chooser

TABLES = ("clientes", "pedidos", "productos", "perfiles", "promociones", "codigos")
CLIENTES_PEDIDOS = ("clientes", "cliente_id", "pedidos", "cliente_id")
PEDIDOS_PRODUCTOS = ("pedidos", "producto", "productos", "producto")
CLIENTES_PERFILES = ("clientes", "cliente_id", "perfiles", "cliente_id")
PEDIDOS_PERFILES = ("pedidos", "cliente_id", "perfiles", "cliente_id")
PEDIDOS_PROMOCIONES = ("pedidos", "producto", "promociones", "producto")


@pytest.fixture
def shop(lab, spark):
    session = lab("stocks_small.csv")
    for name in TABLES:
        session.catalog.register(spark, name, DATA / "joins" / f"{name}.csv", csv_options=CsvOptions(header=True))
    session.scope = CATALOG_SCOPE
    yield session
    session.scope = DATASET_SCOPE
    session.catalog_choices.clear()
    for entry in session.catalog:
        session.catalog.remove(spark, entry.alias)


def confirm(session, *relations):
    for left, left_column, right, right_column in relations:
        r = session.relations.add(session.catalog, left, left_column, right, right_column)
        session.relations.measure(session.catalog, r)
        session.relations.confirm(r, accept_warnings=True)


def ask(session, text, answers=None):
    return session.ask(text, chooser(answers or {}))


def spec_of(session, text, answers=None):
    return session.catalog_interpreter.interpret(parse_question(text), answers or {})


def rows(ev):
    return {r[0]: r[1] for r in ev.result_rows}


def refused(need, *texts):
    assert isinstance(need, NeedsInput) and need.options == [], need
    for text in texts:
        assert text in need.message, need.message
    return need


# --- JOIN types ---------------------------------------------------------------------------

def test_inner_join_one_to_one(shop):
    confirm(shop, CLIENTES_PERFILES)
    question = "¿Cuál es el cupo promedio por segmento?"
    spec = spec_of(shop, question)
    assert (spec.base_table, spec.aggregation, spec.target.columns, spec.group_by) == \
           ("clientes", "AVG", ("clientes.cupo",), "perfiles.segmento")
    assert spec.joins == [Join("INNER", "perfiles", "clientes.cliente_id", "perfiles.cliente_id",
                               "clientes.cliente_id = perfiles.cliente_id")]
    ev = ask(shop, question)
    assert "FROM clientes\nJOIN perfiles ON clientes.cliente_id = perfiles.cliente_id" in ev.sql
    assert "AVG(clientes.cupo)" in ev.sql and "GROUP BY perfiles.segmento" in ev.sql
    assert rows(ev) == {"Plata": 2000, "Oro": 1250, "Bronce": 500}          # Sofia and c6 have no partner


def test_left_join_keeps_entities_without_rows(shop):
    confirm(shop, CLIENTES_PEDIDOS)
    ev = ask(shop, "¿Cuántos pedidos hizo cada cliente, incluyendo los que no tienen pedidos?")
    spec = spec_of(shop, "¿Cuántos pedidos hizo cada cliente, incluyendo los que no tienen pedidos?")
    assert (spec.base_table, spec.joins[0].kind, spec.count_column) == ("clientes", "LEFT", "pedidos.cliente_id")
    assert "LEFT JOIN pedidos ON clientes.cliente_id = pedidos.cliente_id" in ev.sql
    assert "COUNT(pedidos.cliente_id)" in ev.sql
    assert rows(ev) == {"Marta": 3, "Ana": 2, "Luis": 1, "Pedro": 0, "Sofia": 0}
    inner = ask(shop, "¿Cuántos pedidos hizo cada cliente?")                 # INNER: only clients with orders
    assert rows(inner) == {"Marta": 3, "Ana": 2, "Luis": 1} and "COUNT(*)" in inner.sql
    assert "se cuentan los registros de pedidos" in inner.interpretation


def test_entities_without_related_rows(shop):
    confirm(shop, CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS)
    for question in ("¿Qué clientes no tienen pedidos?", "Clientes sin pedidos", "¿Qué clientes no han hecho pedidos?"):
        spec = spec_of(shop, question)
        assert (spec.intent, spec.base_table, spec.joins[0].kind) == (s.FILTER_ROWS, "clientes", "ANTI"), question
        ev = ask(shop, question)
        assert "FROM clientes\nLEFT ANTI JOIN pedidos ON clientes.cliente_id = pedidos.cliente_id" in ev.sql
        assert sorted(r[1] for r in ev.result_rows) == ["Pedro", "Sofia"]
    assert ask(shop, "¿Cuántos clientes no tienen pedidos?").value == 2
    assert [r[0] for r in ask(shop, "¿Qué productos no tienen pedidos?").result_rows] == ["Monitor"]


# --- aggregations and the duplication guard ---------------------------------------------------

def test_aggregation_over_one_to_many(shop):
    confirm(shop, CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS)
    ev = ask(shop, "¿Cuál es el total de unidades por ciudad?")
    assert rows(ev) == {"Bogota": 9, "Medellin": 2}                      # pedido 107 (client 9) has no partner
    assert "SUM(pedidos.unidades)" in ev.sql and "FROM pedidos\nJOIN clientes" in ev.sql
    top = ask(shop, "¿Qué cliente tiene el mayor importe total?")
    assert (top.value, top.intent, top.result_rows[0][1]) == ("Luis", s.GROUP_TOP, 6000)
    three = ask(shop, "¿Cuál es el total de unidades por categoria en Bogota?")
    assert rows(three) == {"Accesorios": 8, "Computo": 1}
    assert three.tables_used == ["pedidos", "productos", "clientes"] and "WHERE clientes.ciudad = 'Bogota'" in three.sql


def test_measure_of_the_one_side_is_not_summed(shop):
    confirm(shop, CLIENTES_PEDIDOS)
    pick = {"table:producto": "pedidos"}
    for question in ("¿Cuál es el total del cupo por producto?", "¿Cuál es el cupo promedio por producto?"):
        refused(spec_of(shop, question, pick), "Los datos de clientes se repiten al unirlos con pedidos", "1:N", "inflado")
    # MAX does not change with repetitions: allowed. Laptop: Ana 1000, Luis 2000; Mouse: Ana 1000, Marta 1500;
    # Teclado: Marta 1500 (pedido 107 has no client). (Phase 4: 'maximo por X' is MAX per group, no menu.)
    ev = ask(shop, "¿Cuál es el cupo máximo por producto?", pick)
    assert rows(ev) == {"Laptop": 2000, "Mouse": 1500, "Teclado": 1500} and "MAX(clientes.cupo)" in ev.sql
    # A single record is not inflated either, and the shared column names stay apart.
    ev = ask(shop, "¿Qué cliente hizo el pedido con mayor importe?")
    assert (ev.intent, ev.value) == (s.RECORD_EXTREME, "Luis") and ev.result_rows[0][0] == 103
    assert "clientes.cliente_id AS clientes__cliente_id" in ev.sql and "pedidos.cliente_id AS pedidos__cliente_id" in ev.sql


def test_many_to_many_is_not_aggregated(shop):
    confirm(shop, PEDIDOS_PROMOCIONES)
    assert shop.relations.find(*PEDIDOS_PROMOCIONES).metrics.cardinality == "N:M"
    for question in ("¿Cuál es el total de unidades por campana?", "¿Cuántos pedidos hay por campana?"):
        refused(spec_of(shop, question), "es N:M", "no es segura para agregar")


# --- clarifications and refusals ------------------------------------------------------------------

def test_ambiguous_column_is_asked(shop):
    confirm(shop, PEDIDOS_PRODUCTOS, PEDIDOS_PROMOCIONES)
    question = "¿Cuál es el total de unidades por producto?"
    need = spec_of(shop, question)
    assert need.key == "table:producto" and [o for _, o in need.options] == ["pedidos", "productos", "promociones"]
    one = spec_of(shop, question, {"table:producto": "pedidos"})
    assert (one.base_table, one.joins, one.group_by) == ("pedidos", [], "producto")    # no JOIN needed
    expected = {"Mouse": 6, "Laptop": 3, "Teclado": 3}
    assert rows(ask(shop, question, {"table:producto": "pedidos"})) == expected
    shop.catalog_choices.clear()
    joined = ask(shop, question, {"table:producto": "productos"})
    assert rows(joined) == expected and "JOIN productos ON pedidos.producto = productos.producto" in joined.sql
    refused(spec_of(shop, question, {"table:producto": "promociones"}), "N:M")


def test_two_join_paths_are_asked(shop):
    confirm(shop, CLIENTES_PEDIDOS, CLIENTES_PERFILES, PEDIDOS_PERFILES)
    question = "¿Cuál es el total de unidades por segmento?"
    need = spec_of(shop, question)
    assert need.key == "path:pedidos|perfiles" and len(need.options) == 2
    direct, through = [value for _, value in need.options][::-1]
    assert direct == "pedidos.cliente_id = perfiles.cliente_id"
    for path, joins in ((direct, 1), (through, 2)):
        shop.catalog_choices.clear()
        ev = ask(shop, question, {"path:pedidos|perfiles": path})
        assert rows(ev) == {"Oro": 9, "Plata": 2} and len(ev.joins) == joins


def test_without_a_confirmed_relation_nothing_is_joined(shop):
    question = "¿Cuál es el total de unidades por ciudad?"
    refused(spec_of(shop, question), "No hay una relacion confirmada entre pedidos y clientes")
    pending = shop.relations.add(shop.catalog, *CLIENTES_PEDIDOS)
    shop.relations.measure(shop.catalog, pending)              # proposed and measured, never confirmed
    need = refused(spec_of(shop, question), "esta PENDIENTE")
    assert "Relaciones entre datasets" in need.missing[0]
    ev = shop.solve_question(question, chooser({}), selected="Bogota")
    assert ev.validation == v.UNRESOLVED and "No hay una relacion confirmada" in ev.validation_note


def test_incompatible_key_types_are_never_joined(shop):
    relation = shop.relations.add(shop.catalog, "clientes", "cliente_id", "codigos", "cliente_id")
    shop.relations.measure(shop.catalog, relation)
    question = "¿Cuál es la tasa promedio por ciudad?"
    refused(spec_of(shop, question), "No hay una relacion confirmada entre codigos y clientes", "Tipos incompatibles")
    relation.status = "CONFIRMADA"                     # even forced past the confirmation rules
    refused(spec_of(shop, question), "No hay una relacion confirmada")


def test_equivalent_questions_give_the_same_query(shop):
    confirm(shop, CLIENTES_PEDIDOS)
    questions = ["¿Cuál es el total de unidades por ciudad?", "Suma de unidades por ciudad", "Unidades totales por ciudad"]
    specs = [spec_of(shop, q).to_dict() for q in questions]
    assert specs[0] == specs[1] == specs[2]
    assert {ask(shop, q).sql for q in questions} == {build_sql(spec_of(shop, questions[0])).sql}


# --- workshop: open, multiple choice, true/false, evidence and export -----------------------------------------

def test_open_multiple_choice_and_true_false(shop):
    confirm(shop, CLIENTES_PEDIDOS)
    base = "¿Qué cliente tiene el mayor importe total?"
    ev = shop.solve_question(base, chooser({}), selected="luis")
    assert ev.validation == v.CORRECT and ev.tables_used == ["pedidos", "clientes"]
    mc = base + "\nA. Ana\nB. Luis\nC. Marta\nD. Pedro"
    ev = shop.solve_question(mc, chooser({}), selected="A")
    assert (ev.correct_answer, ev.validation) == ("B) Luis", v.INCORRECT)
    assert shop.solve_question(mc, chooser({}), selected="b").validation == v.CORRECT
    true = shop.solve_question("Luis es el cliente con el mayor importe total", chooser({}), selected="V")
    false = shop.solve_question("Marta es el cliente con el mayor importe total", chooser({}), selected="V")
    assert (true.verdict, true.validation, false.verdict, false.validation) == ("VERDADERO", v.CORRECT, "FALSO", v.INCORRECT)
    anti = shop.solve_question("¿Qué cliente no tiene pedidos?\nA. Ana\nB. Pedro\nC. Luis\nD. Marta", chooser({}), selected="B")
    assert (anti.correct_answer, anti.validation) == ("B) Pedro", v.CORRECT)
    count = shop.solve_question("Los clientes sin pedidos son 2", chooser({}), selected="V")
    assert (count.verdict, count.validation) == ("VERDADERO", v.CORRECT)


def test_export_of_a_multi_table_question(shop, spark, tmp_path):
    confirm(shop, CLIENTES_PEDIDOS)
    shop.history.clear()
    shop.solve_question("¿Qué cliente tiene el mayor importe total?", chooser({}), selected="Luis")
    shop.solve_question("¿Qué clientes no tienen pedidos?", chooser({}))
    folder = export_workshop(shop, tmp_path)
    data = json.loads((folder / "evidencia.json").read_text(encoding="utf-8"))
    first = data["preguntas"][0]
    assert first["tables_used"] == ["pedidos", "clientes"]
    assert first["relations_used"] == ["clientes.cliente_id = pedidos.cliente_id"]
    assert first["joins"] == ["JOIN clientes ON pedidos.cliente_id = clientes.cliente_id"]
    assert first["spec"]["joins"][0]["kind"] == "INNER" and data["preguntas"][1]["joins"][0].startswith("LEFT ANTI JOIN")
    text = (folder / "evidencia.txt").read_text(encoding="utf-8-sig")
    assert "Tablas: pedidos, clientes" in text and "Relacion confirmada: clientes.cliente_id = pedidos.cliente_id" in text
    sql = (folder / "taller.sql").read_text(encoding="utf-8-sig")
    statements = ["\n".join(l for l in st.splitlines() if not l.startswith("--")).strip() for st in sql.split(";")]
    statements = [st for st in statements if st]
    assert any("JOIN clientes" in st for st in statements)
    for statement in statements:
        spark.sql(statement).collect()                     # every exported statement runs again as written


# --- single table and messages ----------------------------------------------------------------

def test_single_table_queries_are_unchanged(shop):
    spec = QuerySpec(s.GROUP_TOP, s.TOP, "SUM", Metric("column", ("Volume",)), group_by="Company", answer="label")
    assert build_sql(spec).sql == ("SELECT Company,\n       SUM(Volume) AS sum_volume\nFROM dataset\nGROUP BY Company\n"
                                   "ORDER BY sum_volume DESC NULLS LAST, Company ASC\nLIMIT 1")
    shop.scope = DATASET_SCOPE                              # the active dataset, as always
    ev = ask(shop, "¿Qué empresa tiene el mayor volumen total?")
    assert ev.value == "NVDA" and "FROM dataset" in ev.sql and "JOIN" not in ev.sql and ev.tables_used == []
    shop.scope = CATALOG_SCOPE                              # one catalog table: the same query on its view
    ev = ask(shop, "¿Cuál es el promedio de unidades?")
    assert ev.sql == "SELECT AVG(unidades) AS avg_unidades\nFROM pedidos" and ev.value == pytest.approx(12 / 7)


def test_messages_for_the_student(shop, spark):
    need = spec_of(shop, "¿Qué hora es?")
    assert isinstance(need, NeedsInput) and need.message == "Pregunta no reconocida automaticamente."
    for entry in shop.catalog:
        shop.catalog.remove(spark, entry.alias)
    need = spec_of(shop, "¿Cuántos pedidos hay?")
    assert need.message == "No hay datasets registrados en el catalogo." and "opcion" not in need.message
    assert "Herramientas avanzadas > 11" in need.missing[0]


def test_builder_validates_the_join():
    assert ident("clientes.cliente_id") == "clientes.cliente_id" and ident("t.select") == "t.`select`"
    assert output_names(["a.id", "b.id", "a.x"]) == {"a.id": "a__id", "b.id": "b__id", "a.x": "x"}
    spec = QuerySpec(s.COUNT_ROWS, s.SCALAR, "COUNT", base_table="a", joins=[Join("FULL", "b", "a.k", "b.k")])
    with pytest.raises(ValueError, match="no admitido"):
        from_sql(spec)


MENU_SCRIPT = ["14", "", "14", "", "0"]


def test_menu_switches_the_scope(shop, monkeypatch, capsys):
    from app.ui import menu

    shop.scope = DATASET_SCOPE
    answers = iter(MENU_SCRIPT)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    seen = []
    real = menu.switch_scope
    monkeypatch.setattr(menu, "switch_scope", lambda session: (real(session), seen.append(session.scope)))
    menu.run(shop)
    out = capsys.readouterr().out
    assert seen == [CATALOG_SCOPE, DATASET_SCOPE] and next(answers, None) is None
    assert "Las preguntas se responden sobre el catalogo" in out and "sobre el dataset activo" in out
