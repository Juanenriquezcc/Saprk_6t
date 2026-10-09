"""Phase 5.5: several datasets in one workshop, automatic choice of the datasets of each question,
unknown context words and options marked '+D.'.

tests/data/multi (expected values by hand):
  ventas    V1 Laptop 2 Entregado, V2 Mouse 5 Entregado, V3 Mouse 3 Pendiente, V4 Teclado 4 Entregado,
            V5 Laptop 1 Devuelto -> delivered units 2+5+4 = 11 (3 orders), Mouse 5 the top; all 15; AVG 3, MAX 5, MIN 1
  compras   qty 10 + 20 + 5 = 35 ('qty' and 'quantity' both mean units: never chosen silently)
  clientes  C1 Ana, C2 Luis, C3 Marta;  pedidos P1 C1 100, P2 C1 40, P3 C2 70, P4 C3 30
            -> amount per name Ana 140, Luis 70, Marta 30
  productos Laptop Computo, Mouse Accesorios, Teclado Accesorios -> per category Accesorios 2, Computo 1
Exam (tests/data/examen): reference result of the units question is Smartphone X (option B).
"""
import pytest

import exam_acceptance as ex
from app.catalog import DatasetCatalog
from app.questions import validator as v
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from app.session import AUTO_SCOPE, LabSession
from app.spark.loader import CsvOptions
from conftest import DATA, chooser

MULTI = DATA / "multi"
COLOMBIA = ("¿Cuál es el producto con mayor cantidad de unidades vendidas en Colombia, "
            "considerando únicamente pedidos entregados?")
OPTIONS_PLUS = "\nA. Monitor 24\nB. Smartphone X\nC. Audifonos Bluetooth\n+D. Laptop Pro 14"
OPTIONS_PLAIN = "\nA. Monitor 24\nB. Smartphone X\nC. Audifonos Bluetooth\nD. Laptop Pro 14"


@pytest.fixture
def workshop_of(spark):
    """workshop_of('ventas', 'clientes') -> LabSession of a multi-dataset workshop (first = active)."""
    catalogs = []

    def make(*names):
        catalog = DatasetCatalog()
        for name in names:
            catalog.register(spark, name, MULTI / f"{name}.csv", csv_options=CsvOptions(header=True))
        catalogs.append(catalog)
        return LabSession.from_catalog(spark, catalog, names[0])
    yield make
    for catalog in catalogs:
        for entry in catalog:
            catalog.remove(spark, entry.alias)


def ask(session, text, answers=None):
    return session.ask(text, chooser(answers or {}))


def rows(ev):
    return {r[0]: r[1] for r in ev.result_rows}


# --- registering several datasets (the real console flow) -------------------------------------------

def test_register_several_datasets_validating_every_input(spark, monkeypatch, capsys):
    from app.ui import datasets as datasets_ui
    catalog = DatasetCatalog()
    script = ["abc", "3",                                                      # invalid count, then 3
              str(MULTI / "no_existe.csv"), str(MULTI / "ventas.csv"), "",     # missing file, then ventas; CSV ok
              "select", "ventas", "n",                                         # invalid alias, then ventas; no ETL
              str(MULTI / "ventas.csv"), "n",                                  # the same file again: refused
              str(MULTI / "clientes.csv"), "", "ventas", "", "n",              # duplicate alias -> suggested one
              ""]                                                              # the third one is cancelled
    answers = iter(script)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    try:
        added = datasets_ui.register_many(spark, catalog)
        out = capsys.readouterr().out
        assert next(answers, None) is None
        assert [e.alias for e in added] == [e.alias for e in catalog] == ["ventas", "clientes"]
        for text in ("Escriba un numero entero positivo", "No existe", "Alias no valido",
                     "ya esta registrado como 'ventas'", "Ya existe un dataset con el alias 'ventas'",
                     "Registro cancelado: se continua con los datasets ya registrados"):
            assert text in out, text
        assert catalog.get("ventas").df.columns == ["order_id", "product", "quantity", "status", "city"]
        session = LabSession.from_catalog(spark, catalog, "ventas")
        assert (session.scope, session.active_alias, session.uses_catalog) == (AUTO_SCOPE, "ventas", True)
        assert spark.table("dataset").count() == 5                              # the active one is the view 'dataset'
    finally:
        for entry in catalog:
            catalog.remove(spark, entry.alias)


# --- automatic choice of the datasets of a question -------------------------------------------------

def test_1_one_sales_dataset_is_resolved_alone(workshop_of):
    session = workshop_of("ventas")
    assert not session.uses_catalog                                           # one dataset: as always
    ev = ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?")
    assert ev.value == 11 and ev.sql == "SELECT SUM(quantity) AS sum_quantity\nFROM dataset\nWHERE status = 'Entregado'"


def test_2_only_one_dataset_has_the_columns(workshop_of):
    session = workshop_of("ventas", "clientes")
    assert session.uses_catalog
    ev = ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?")
    assert (ev.value, ev.tables_used) == (11, ["ventas"]) and "FROM ventas" in ev.sql
    top = ask(session, "¿Qué producto tiene más unidades en pedidos entregados?")
    assert (top.value, top.result_rows[0][1], top.spec["aggregation"]) == ("Mouse", 5, "SUM")
    # the automatic choice does not change the operation
    assert ask(session, "¿Cuántos pedidos entregados hay?").value == 3
    assert ask(session, "¿Cuál es el promedio de quantity?").value == 3
    assert ask(session, "¿Cuál es el máximo de quantity?").value == 5
    assert ask(session, "¿Cuál es el mínimo de quantity?").value == 1


def test_3_similar_columns_in_two_datasets_are_asked(workshop_of):
    session = workshop_of("ventas", "compras")
    need = session.active_interpreter.interpret(parse_question("¿Cuántas unidades hay en total?"))
    assert isinstance(need, NeedsInput) and need.key == "col:QUANTITY"
    assert [label.split("[dataset: ")[1] for label, _ in need.options] == ["ventas]", "compras]"]
    assert ask(session, "¿Cuántas unidades hay en total?", {"col:QUANTITY": "qty"}).value == 35
    session.catalog_choices.clear()
    ev = ask(session, "¿Cuántas unidades hay en total?", {"col:QUANTITY": "quantity"})
    assert (ev.value, ev.tables_used) == (15, ["ventas"])
    assert ev.clarifications == ["Se encontraron varias columnas posibles para 'unidades'. Seleccione una. "
                                 "-> quantity  (nombre = 'quantity')  [dataset: ventas]"]     # recorded in the evidence


def test_4_join_through_a_confirmed_relation(workshop_of):
    session = workshop_of("clientes", "pedidos")
    relation = session.relations.propose(session.catalog)[0]
    session.relations.measure(session.catalog, relation)
    session.relations.confirm(relation, accept_warnings=True)
    ev = ask(session, "¿Cuál es el importe total por nombre?")
    assert rows(ev) == {"Ana": 140, "Luis": 70, "Marta": 30}
    assert "FROM pedidos\nJOIN clientes ON pedidos.cliente_id = clientes.cliente_id" in ev.sql
    assert ev.relations_used == ["clientes.cliente_id = pedidos.cliente_id"]


@pytest.mark.parametrize("decision", ["pending", "rejected"])
def test_5_pending_or_rejected_relation_is_never_used(workshop_of, decision):
    session = workshop_of("clientes", "pedidos")
    relation = session.relations.propose(session.catalog)[0]
    session.relations.measure(session.catalog, relation)
    if decision == "rejected":
        session.relations.reject(relation)
    need = session.active_interpreter.interpret(parse_question("¿Cuál es el importe total por nombre?"))
    assert isinstance(need, NeedsInput) and need.options == []
    assert ("esta PENDIENTE" if decision == "pending" else "esta RECHAZADA") in need.message


def test_6_dataset_added_after_previous_questions(workshop_of, spark):
    session = workshop_of("ventas", "clientes")
    assert ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?").value == 11
    session.catalog.register(spark, "productos", MULTI / "productos.csv", csv_options=CsvOptions(header=True))
    ev = ask(session, "¿Cuántos registros hay por categoria?")
    assert rows(ev) == {"Accesorios": 2, "Computo": 1} and ev.tables_used == ["productos"]
    assert ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?").value == 11   # still works


def test_7_explicit_alias_in_the_question(workshop_of):
    session = workshop_of("ventas", "compras")
    ev = ask(session, "¿Cuántos registros tiene compras?")
    assert (ev.value, ev.tables_used) == (3, ["compras"])
    ev = ask(session, "¿Cuántos registros tiene ventas?")
    assert (ev.value, ev.tables_used) == (5, ["ventas"])


def test_8_missing_column_is_never_answered_arbitrarily(workshop_of):
    session = workshop_of("ventas", "clientes")
    need = session.active_interpreter.interpret(parse_question("¿Cuál es el promedio de margen?"))
    assert isinstance(need, NeedsInput) and "menu 10" in need.message
    assert [label for label, _ in need.options] == ["quantity  [dataset: ventas]"]
    need = session.active_interpreter.interpret(parse_question("¿Qué hora es?"))
    assert need.key == "unrecognized" and "ventas (order_id, product, quantity, status, city)" in need.missing[-1]


def test_9_colombia_is_context_only_by_decision(workshop_of):
    session = workshop_of("ventas", "clientes")
    need = session.active_interpreter.interpret(parse_question(COLOMBIA))
    assert need.key == "unknown:colombia" and "contexto general" in need.message and "cancele" in need.message
    cancelled = session.solve_question(COLOMBIA, lambda need: None, selected="Mouse")
    assert cancelled.validation == v.UNRESOLVED and cancelled.sql == "" and cancelled.value is None
    ev = session.solve_question(COLOMBIA, lambda need: "ignore", selected="Mouse")
    assert (ev.value, ev.result_rows[0][1], ev.validation) == ("Mouse", 5, v.CORRECT)
    assert "SUM(quantity)" in ev.sql and "WHERE status = 'Entregado'" in ev.sql and "GROUP BY product" in ev.sql
    assert any("'Colombia' no se uso como filtro" in w for w in ev.warnings)          # the decision is in the evidence
    assert ev.clarifications and "-> Es contexto general" in ev.clarifications[0]


# --- options A-D -------------------------------------------------------------------------------------

@pytest.mark.parametrize("options, marked", [
    (OPTIONS_PLUS, "D"),
    (OPTIONS_PLAIN, None),
    ("\nA) Monitor 24  \nB) Smartphone X\nC) Audifonos Bluetooth   \nD) Laptop Pro 14 [correcta]", "D"),
    ("\n+A. Monitor 24\nB. Smartphone X\nC. Audifonos Bluetooth\nD. Laptop Pro 14", "A"),
])
def test_options_are_read_clean(options, marked):
    p = parse_question(COLOMBIA + options)
    assert p.options == [("A", "Monitor 24"), ("B", "Smartphone X"), ("C", "Audifonos Bluetooth"), ("D", "Laptop Pro 14")]
    assert (p.marked_option, p.options_issue, p.body.startswith("¿Cuál es el producto")) == (marked, None, True)


def test_repeated_option_letters_are_reported():
    p = parse_question(COLOMBIA + "\nA. Monitor 24\nA. Smartphone X\nB. Laptop Pro 14")
    assert p.options_issue == "letras de opcion repetidas (A)"


@pytest.fixture(scope="module")
def exam(spark):
    return ex.analyzer_etl(spark, ex.RULES + ex.EQUIVALENCES, confirm_equivalences=True)


@pytest.mark.parametrize("options, material", [(OPTIONS_PLUS, "D) Laptop Pro 14"), (OPTIONS_PLAIN, None)])
def test_10_11_exam_question_with_four_options(spark, exam, options, material):
    reference = ex.as_result(ex.run_reference(spark, ex.reference_clean(spark), ex.QUERIES[0]))
    assert max(reference, key=reference.get) == "Smartphone X"                 # independent reference
    exam.df.createOrReplaceTempView("dataset")
    exam.choices.clear()
    ev = exam.solve_question(COLOMBIA + options, chooser({"unknown:colombia": "ignore"}), selected="B")
    assert [o[0] for o in ev.options] == ["A", "B", "C", "D"]
    assert (ev.correct_answer, ev.selected_answer, ev.validation) == ("B) Smartphone X", "B) Smartphone X", v.CORRECT)
    assert ev.material_option == material                                       # never used to validate
    assert any("El material marca la opcion D" in w for w in ev.warnings) == (material is not None)
    wrong = exam.solve_question(COLOMBIA + options, chooser({}), selected="D")
    assert (wrong.correct_answer, wrong.validation) == ("B) Smartphone X", v.INCORRECT)


# --- the single-dataset flow and the workshop menu ------------------------------------------------------

def test_12_single_dataset_flow_is_unchanged(lab, spark):
    session = lab("multi/ventas.csv")
    assert (session.scope, session.active_alias, session.uses_catalog) == (AUTO_SCOPE, None, False)
    ev = ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?")
    assert ev.value == 11 and "FROM dataset" in ev.sql and ev.tables_used == []
    # another dataset added: the active one is still used until the user adopts it into the catalog
    session.catalog.register(spark, "clientes", MULTI / "clientes.csv")
    try:
        assert not session.uses_catalog
        assert "FROM dataset" in ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?").sql
        session.catalog.adopt(spark, "ventas", session.load, session.profile, session.semantic, session.etl)
        session.active_alias = "ventas"
        ev = ask(session, "¿Cuántas unidades se vendieron en pedidos entregados?")
        assert ev.value == 11 and "FROM ventas" in ev.sql
    finally:
        session.active_alias = None
        for entry in session.catalog:
            session.catalog.remove(spark, entry.alias)
        assert spark.table("dataset").count() == 5          # the adopted data is still the active dataset


def test_workshop_menu_manages_datasets(workshop_of, monkeypatch, capsys):
    from app.ui import workshop
    session = workshop_of("ventas", "clientes")
    script = ["10", "1", "",                     # see the datasets
              "4", "2", "",                      # the active dataset becomes clientes
              "5", "2", "",                      # questions only on the active dataset
              "5", "1", "",                      # back to the automatic choice
              "0", "0"]
    answers = iter(script)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    workshop.run(session)
    out = capsys.readouterr().out
    assert next(answers, None) is None and "Traceback" not in out
    assert "Catalogo: ventas (activo), clientes" in out and "Dataset activo: clientes" in out
    assert "SOLO EL DATASET ACTIVO" in out and session.scope == AUTO_SCOPE and session.active_alias == "clientes"


def test_start_prompt_offers_several_datasets(monkeypatch, capsys):
    from app.application import Application
    answers = iter(["varios"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert Application()._load(None) == "varios"                     # -> the several-datasets flow
    assert "Varios datasets en el mismo taller: escriba 'varios'" in capsys.readouterr().out
    answers = iter(["salir"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert Application()._load(None) is None                          # leaving still works
