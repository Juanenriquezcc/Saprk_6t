"""Interpreter + resolver: intents, ambiguity, multiple choice and true/false."""
import pytest

from app.query import spec as s
from app.questions.intents import NeedsInput
from app.questions.parser import parse_question
from conftest import chooser


def interpret(session, text, extra=None):
    return session.interpreter.interpret(parse_question(text), extra)


# --- intents ------------------------------------------------------------------

@pytest.mark.parametrize("question, intent, shape, agg", [
    ("¿Cuántos registros hay?", s.COUNT_ROWS, s.SCALAR, "COUNT"),
    ("¿Cuántas empresas distintas hay?", s.COUNT_DISTINCT, s.SCALAR, "COUNT_DISTINCT"),
    ("¿Cuál es el promedio de Close?", s.AGG_SCALAR, s.SCALAR, "AVG"),
    ("¿Cuántos registros tienen Close mayor que Open?", s.COUNT_WHERE, s.SCALAR, "COUNT"),
    ("¿Cuál es el volumen total de AAPL?", s.AGG_WHERE, s.SCALAR, "SUM"),
    ("Promedio de cierre por empresa", s.GROUP_AGG, s.GROUP, "AVG"),
    ("¿Qué empresa tiene el mayor volumen total?", s.GROUP_TOP, s.TOP, "SUM"),
    ("¿Cuál es el registro con mayor volumen?", s.RECORD_EXTREME, s.RECORD, None),
    ("¿Cuál fue el mayor rango entre High y Low?", s.DIFFERENCE, s.SCALAR, "MAX"),
    ("¿Cuál es la variación porcentual promedio entre apertura y cierre?", s.PCT_CHANGE, s.SCALAR, "AVG"),
    ("¿Qué empresa tuvo la mayor variación porcentual del cierre en el periodo?", s.PCT_CHANGE_PERIOD, s.TOP, "PERIOD_CHANGE"),
    ("¿Qué porcentaje de registros tiene Close > Open?", s.PERCENTAGE_OF, s.SCALAR, "PERCENT"),
    ("¿Cuál es mayor en promedio, Open o Close?", s.COLUMN_COMPARISON, s.COMPARE, "AVG"),
    ("¿Qué empresa tiene más registros?", s.GROUP_TOP, s.TOP, "COUNT"),
    ("Which company has the highest total volume?", s.GROUP_TOP, s.TOP, "SUM"),
    ("How many records have close greater than open?", s.COUNT_WHERE, s.SCALAR, "COUNT"),
])
def test_intents(lab, question, intent, shape, agg):
    spec = interpret(lab("stocks_small.csv"), question)
    assert not isinstance(spec, NeedsInput), spec
    assert (spec.intent, spec.shape, spec.aggregation) == (intent, shape, agg)


def test_record_answer_column(lab):
    session = lab("stocks_small.csv")
    assert interpret(session, "¿En qué fecha se registró el mayor volumen?").answer == "column:Date"
    assert interpret(session, "¿Qué empresa registró el mayor volumen en un solo día?").answer == "column:Company"


def test_conditions_and_dates(lab):
    spec = interpret(lab("stocks_small.csv"), "¿Cuál fue el volumen total de AAPL entre 2024-01-03 y 2024-01-04?")
    assert spec.filter_labels() == ["Company = AAPL", "Date >= 2024-01-03", "Date <= 2024-01-04"]
    spec = interpret(lab("ventas.csv"), "¿Cuántas ventas hubo en febrero de 2024?")
    assert spec.filter_labels() == ["fecha >= 2024-02-01", "fecha <= 2024-02-29"]
    spec = interpret(lab("stocks_small.csv"), "¿Cuántos registros tienen un volumen superior a 2.000?",
                     {"num:2.000": 2000})
    assert spec.filter_labels() == ["Volume > 2000"]


# --- ambiguity: never guess ---------------------------------------------------

def test_generic_word_with_several_columns_asks(lab):
    need = interpret(lab("ventas.csv"), "¿Cuál es el precio promedio?")
    assert isinstance(need, NeedsInput) and need.remember
    assert need.message == "Se encontraron varias columnas posibles para 'precio'. Seleccione una."
    assert [v for _, v in need.options] == ["precio_unitario", "precio_total"]


def test_price_in_stock_data_asks_between_four_columns(lab):
    need = interpret(lab("stocks_small.csv"), "¿Cuál fue el precio máximo?")
    assert isinstance(need, NeedsInput)
    assert [v for _, v in need.options] == ["Open", "High", "Low", "Close"]


def test_choice_is_remembered_for_the_session(lab):
    session = lab("ventas.csv")
    choose = chooser({"col:generic:precio": "precio_total"})
    first = session.ask("¿Cuál es el precio promedio?", choose)
    second = session.ask("¿Cuál es el precio máximo?", choose)
    assert len(choose.asked) == 1                      # asked only once
    assert first.value == pytest.approx(34.25 / 6)
    assert second.value == 12.0


def test_entity_superlative_without_aggregation_asks(lab):
    need = interpret(lab("stocks_small.csv"), "¿Qué empresa tuvo el mayor volumen?")
    assert isinstance(need, NeedsInput) and need.key == "agg"
    assert [v for _, v in need.options] == ["SUM", "AVG", "RECORD"]


def test_ambiguous_number_asks(lab):
    need = interpret(lab("stocks_small.csv"), "¿Cuántos registros tienen un volumen superior a 2.000?")
    assert isinstance(need, NeedsInput) and need.key == "num:2.000"


def test_unrecognized_question_explains_what_is_missing(lab):
    need = interpret(lab("stocks_small.csv"), "¿Qué hora es?")
    assert isinstance(need, NeedsInput) and not need.options
    assert need.message == "Pregunta no reconocida automaticamente."
    assert any("operacion" in m for m in need.missing)


def test_missing_role_is_never_invented(lab):
    need = interpret(lab("ventas.csv"), "¿Qué empresa vendió más?")
    assert isinstance(need, NeedsInput)


# --- answers ------------------------------------------------------------------

def test_multiple_choice_with_rounded_options(lab):
    ev = lab("stocks_small.csv").ask(
        "¿Cuál es el promedio de Close de MSFT?\nA) 248.40\nB) 303.50\nC) 283.40\nD) 293.40", chooser({}))
    assert ev.matched_option == "B" and ev.answer == "B) 303.50"


def test_multiple_choice_tolerates_float_precision(lab):
    ev = lab("stocks_small.csv").ask("¿Cuál es el promedio de Close?\nA) 154.04\nB) 154.40\nC) 145.04\nD) 150", chooser({}))
    assert ev.value == pytest.approx(154.0416667) and ev.matched_option == "A"


def test_multiple_choice_text_options(lab):
    ev = lab("stocks_small.csv").ask("¿Qué empresa tiene el mayor volumen total?\nA. AAPL\nB. MSFT\nC. NVDA\nD. TSLA",
                                     chooser({}))
    assert ev.matched_option == "C"


def test_multiple_choice_no_match_is_not_forced(lab):
    ev = lab("stocks_small.csv").ask("¿Cuántos registros hay?\nA) 10\nB) 11\nC) 13\nD) 14", chooser({}))
    assert ev.matched_option is None
    assert ev.answer == "Ninguna opcion coincide con el resultado calculado."
    assert ev.value == 12


def test_true_false_false_shows_correct_value(lab):
    ev = lab("stocks_small.csv").ask("Los registros donde Close > Open son 15.", chooser({}))
    assert ev.verdict == "FALSO" and ev.value == 8 and ev.correct_value == "8" and ev.claim == "15"
    assert ev.sql.endswith("WHERE Close > Open")


def test_true_false_true(lab):
    session = lab("stocks_small.csv")
    assert session.ask("¿Es verdadero que el volumen total es 22200?", chooser({})).verdict == "VERDADERO"
    assert session.ask("NVDA es la empresa con mayor volumen total", chooser({})).verdict == "VERDADERO"
    assert session.ask("MSFT es la empresa con mayor volumen total", chooser({})).verdict == "FALSO"


def test_spanish_contraction_al_in_conditions(lab):
    spec = interpret(lab("stocks_small.csv"), "¿Cuántos registros tienen el cierre superior al precio de apertura?")
    assert spec.filter_labels() == ["Close > Open"]
    spec = interpret(lab("stocks_small.csv"), "¿Cuántos registros tienen un volumen igual al 1000?")
    assert spec.filter_labels() == ["Volume = 1000"]


def test_record_options_need_every_field_to_agree(lab):
    from app.questions.resolver import match_record_options
    import datetime
    row = {"Date": datetime.date(2024, 1, 4), "Company": "NVDA", "Volume": 4000}
    options = [("A", "NVDA - 2024-01-03"), ("B", "AAPL - 2024-01-04"), ("C", "NVDA - 04/01/2024"), ("D", "NVDA")]
    assert match_record_options(options, row) == ("C", None)
    assert match_record_options([("A", "NVDA"), ("B", "NVDA, 4000 acciones")], row) == ("B", None)
    letter, note = match_record_options([("A", "NVDA"), ("B", "NVDA")], row)
    assert letter is None and "Varias opciones" in note


def test_tie_is_reported(lab):
    ev = lab("ventas.csv").ask("¿Cuál es la categoría con más registros?", chooser({}))
    assert any("Empate" in w for w in ev.warnings)
    assert len(ev.extra_sql) == 1


def test_evidence_contents(lab):
    session = lab("stocks_small.csv")
    ev = session.ask("7. ¿Cuál es el volumen total de AAPL?", chooser({}))
    assert ev.number == 7 and ev.intent == s.AGG_WHERE
    assert ev.columns_used == ["Volume", "Company"] and ev.filters == ["Company = AAPL"]
    assert "WHERE Company = 'AAPL'" in ev.sql and ev.value == 5700
    assert ev.interpretation.startswith("AGG_WHERE:") and ev.timestamp
    assert ev.spec["intent"] == s.AGG_WHERE
    assert session.history == [ev]


def test_group_result_table(lab):
    ev = lab("stocks_small.csv").ask("Promedio de cierre por empresa", chooser({}))
    assert ev.result_columns == ["Company", "avg_close"]
    assert ev.result_rows == [["MSFT", 303.5], ["AAPL", 103.0], ["NVDA", 55.625]]
