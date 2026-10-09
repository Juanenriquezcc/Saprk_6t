"""Regression: "mayor cantidad de unidades" is SUM(quantity) per group, never an operation menu.

ventas_unidades.csv: every reading picks a DIFFERENT product, so only SUM + the filter passes.
  delivered SUM(quantity):  Smartphone X 11 > Monitor 24 10 > Audifonos 4   (Laptop only Cancelado)
  single delivered record:  Monitor 24 (9)       COUNT delivered: Audifonos (4)
  no status filter:         Laptop Pro 14 (20)   highest price:   Laptop Pro 14
  departments, delivered:   Antioquia 10 > Cundinamarca 9 > Valle 6  (Valle 26 without filter)
"""
from app.query import spec as s
from app.questions import validator as v
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from conftest import chooser

DATA = "ventas_unidades.csv"
QUESTION = ("¿Cuál es el producto con mayor cantidad de unidades vendidas en Colombia, "
            "considerando únicamente pedidos entregados?")
MC = QUESTION + "\nA. Monitor 24\nB. Smartphone X\nC. Audifonos Bluetooth\nD. Laptop Pro 14"
DELIVERED = [("status", "=", "Entregado")]
# Phase 4.1: 'Colombia' is no value of the data, so it is asked once (never silently dropped). The
# answer 'not a filter' is the only decision of these tests: the operation is never asked.
COLOMBIA = {"unknown:colombia": "ignore"}


def interpret(session, text, answers=None):
    return session.interpreter.interpret(parse_question(text), answers or {})


def filters(spec):
    return [(f.column, f.op, f.value) for f in spec.filters]


def test_units_question_is_sum_per_product_without_asking(lab):
    session = lab(DATA)
    assert interpret(session, MC).key == "unknown:colombia"
    spec = interpret(session, MC, COLOMBIA)
    assert not isinstance(spec, NeedsInput), spec.message
    assert (spec.intent, spec.shape, spec.aggregation) == (s.GROUP_TOP, s.TOP, "SUM")
    assert spec.target.columns == ("quantity",) and spec.group_by == "product" and filters(spec) == DELIVERED

    choose = chooser(COLOMBIA)                   # fails on any other question (never SUM/AVG)
    ev = session.ask(MC, choose)
    assert [need.key for need in choose.asked] == ["unknown:colombia"]
    assert ev.value == "Smartphone X" and ev.result_rows[0][1] == 11
    assert "SUM(quantity)" in ev.sql and "GROUP BY product" in ev.sql and "status = 'Entregado'" in ev.sql


def test_units_variants(lab):
    session = lab(DATA)
    cases = {
        "¿Cuál es el departamento con mayor cantidad vendida en pedidos entregados?": ("department", "DESC", DELIVERED),
        "¿Qué producto tiene más unidades vendidas?": ("product", "DESC", []),
        "¿Qué producto tiene la menor cantidad de unidades?": ("product", "ASC", []),
        "Which product has the highest quantity?": ("product", "DESC", []),
    }
    for text, (group, order, where) in cases.items():
        spec = interpret(session, text)
        assert not isinstance(spec, NeedsInput), text
        assert (spec.aggregation, spec.target.columns, spec.group_by, spec.order, filters(spec)) == \
               ("SUM", ("quantity",), group, order, where), text
    ev = session.ask("¿Cuál es el departamento con mayor cantidad vendida en pedidos entregados?", chooser({}))
    assert ev.value == "Antioquia" and ev.result_rows[0][1] == 10


def test_number_of_orders_is_still_a_count(lab):
    spec = interpret(lab(DATA), "¿Cuál es el producto con mayor número de pedidos entregados?")
    assert spec.aggregation == "COUNT" and spec.target is None and spec.group_by == "product"
    assert filters(spec) == DELIVERED
    assert lab(DATA).ask("¿Cuál es el producto con mayor número de pedidos entregados?",
                         chooser({})).value == "Audífonos Bluetooth"


def test_price_and_single_record_are_not_summed(lab):
    session = lab(DATA)
    need = interpret(session, "¿Cuál es el producto con mayor precio?")     # ambiguous: still asked
    assert isinstance(need, NeedsInput) and need.key == "agg"
    assert [val for _, val in need.options] == ["SUM", "AVG", "RECORD"]
    ev = session.ask("¿Cuál es el producto con mayor precio?", chooser({"agg": "RECORD"}))
    assert ev.value == "Laptop Pro 14"
    spec = interpret(session, "¿Cuál es el producto con mayor cantidad en un solo pedido?")
    assert spec.intent == s.RECORD_EXTREME and spec.aggregation is None


def test_multiple_choice_validation(lab):
    session = lab(DATA)
    ev = session.solve_question(MC, chooser(COLOMBIA), selected="A")
    assert (ev.correct_answer, ev.selected_answer, ev.validation) == ("B) Smartphone X", "A) Monitor 24", v.INCORRECT)
    assert session.solve_question(MC, chooser({}), selected="b").validation == v.CORRECT   # remembered: no question


def test_workshop_inputs_stay_in_sync(lab, monkeypatch, capsys):
    """Before the fix an extra 'Que calculo desea' prompt ate the next answer and shifted every input."""
    from app.ui import workshop

    session = lab(DATA)
    script = ["1",
              *MC.splitlines(), "", "", "A", "1", "",      # block, type OK, student A, 'Colombia' is no filter, next
              *MC.splitlines(), "", "", "B", "",           # the decision is remembered: no extra input
              "¿Cuál es el departamento con mayor cantidad vendida en pedidos entregados?", "", "", "Antioquia", "0",
              "0", "n"]                                    # exit, do not export
    answers = iter(script)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    workshop.run(session)
    out = capsys.readouterr().out
    assert next(answers, None) is None and "Traceback" not in out and "Que calculo desea" not in out
    assert [e.validation for e in session.questions] == [v.INCORRECT, v.CORRECT, v.CORRECT]
    assert [e.value for e in session.questions] == ["Smartphone X", "Smartphone X", "Antioquia"]
