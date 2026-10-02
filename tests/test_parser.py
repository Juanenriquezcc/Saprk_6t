"""Question parser and number reading (pure Python, no Spark)."""
import pytest

from app.query import spec as s
from app.questions.numbers import format_number, matches, read_number
from app.questions.parser import parse_question


# --- numbers ------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("15", [15.0]),
    ("273.40", [273.4]),
    ("1.234,56", [1234.56]),
    ("1,234.56", [1234.56]),
    ("12,5%", [12.5]),
    ("$1.000.000", [1000000.0]),
    ("-3.5", [-3.5]),
    ("0,750", [0.75]),
    ("1.234", [1.234, 1234.0]),      # ambiguous: both readings
    ("abc", []),
    ("12.", []),
])
def test_read_number(text, expected):
    assert [r.value for r in read_number(text)] == expected


def test_matches_uses_written_precision():
    r = read_number("273.40")[0]
    assert matches(273.4012, r) and matches(273.396, r)
    assert not matches(273.41, r)
    assert matches(15.0, read_number("15")[0])
    assert not matches(16.0, read_number("15")[0])


def test_format_number():
    assert format_number(22200.0) == "22200"
    assert format_number(154.041666666) == "154.0417"
    assert format_number(None) == "NULL"


# --- parser -------------------------------------------------------------------

def test_multiple_choice_options_on_lines():
    p = parse_question("¿Cuál fue el precio máximo?\nA. 100\nB) 200\nC: 300\nD - 400")
    assert p.kind == s.MULTIPLE_CHOICE
    assert p.options == [("A", "100"), ("B", "200"), ("C", "300"), ("D", "400")]
    assert p.body == "¿Cuál fue el precio máximo?"


def test_multiple_choice_inline_options():
    p = parse_question("¿Cuántos registros hay? A) 10 B) 12 C) 14 D) 16")
    assert p.kind == s.MULTIPLE_CHOICE and [o[0] for o in p.options] == ["A", "B", "C", "D"]
    assert p.options[1] == ("B", "12")


def test_question_number_prefix():
    p = parse_question("7. ¿Cuál es el volumen total?")
    assert p.number == 7 and p.body.startswith("¿Cuál")


@pytest.mark.parametrize("text, claim, body_start", [
    ("Los registros donde Close > Open son 15.", "15", "Los registros donde Close > Open"),
    ("¿Es verdadero que el volumen total es 22200?", "22200", "el volumen total"),
    ("Verdadero o falso: el promedio de Close es 154,04", "154,04", "el promedio de Close"),
    ("NVDA es la empresa con mayor volumen total", "NVDA", "la empresa con mayor volumen total"),
    ("La empresa con mayor volumen total es NVDA.", "NVDA", "La empresa con mayor volumen total"),
])
def test_true_false_claims(text, claim, body_start):
    p = parse_question(text)
    assert p.kind == s.TRUE_FALSE
    assert p.claim == claim
    assert p.body.startswith(body_start)


def test_true_false_marker_lines_and_instructions():
    p = parse_question("¿El total de ventas es 15?\nV/F\nSi es falso escribir la respuesta.")
    assert p.kind == s.TRUE_FALSE and p.claim == "15"
    assert "V/F" not in p.body and "falso" not in p.normalized
    p = parse_question("El promedio de Close es 154 (V/F)")
    assert p.kind == s.TRUE_FALSE and p.claim == "154"


def test_claim_written_as_a_question():
    # Not an interrogative and ends with a number: a claim even without a V/F mark.
    p = parse_question("¿Los registros con cierre mayor a la apertura son 15?")
    assert p.kind == s.TRUE_FALSE and p.claim == "15"
    # Interrogatives ask for a value: never a claim.
    for text in ("¿Cuántos registros son 15?", "¿Cuál es el valor 15?", "¿Qué empresa es NVDA?"):
        assert parse_question(text).kind == s.OPEN, text


def test_open_question_is_not_a_claim():
    p = parse_question("¿Cuál es la empresa con mayor volumen total?")
    assert p.kind == s.OPEN and p.claim is None


def test_normalized_text_keeps_numbers_and_operators():
    p = parse_question("¿Registros con Close >= 1.234,5 y Open < 10?")
    assert "1.234,5" in p.normalized and ">=" in p.normalized and "<" in p.normalized
    assert "¿" not in p.normalized and "?" not in p.normalized
