"""Phase 5.1: explicit, confirmed equivalences of descriptive values (never automatic, never on codes).

equivalencias.csv, by hand:
  codigo_ref producto        referencia cantidad
  1          Laptop Pro 14   R-001      2
  2          Laptop Pro14    R-002      1
  3          Laptop Pro 14   R-003      3
  4          Mouse X         R-004      1
  5          MouseX          R-005      4
  6          Mouse           R-006      2
  Laptop Pro 14 = 5 units (+1 of 'Laptop Pro14' = 6 when unified); Mouse X 1, MouseX 4, Mouse 2.
Exam: tests/exam_acceptance.py (reference ETL, ten queries, EQUIVALENCES of the case).
"""
import json

import pytest

import exam_acceptance as ex
from app.errors import AppError
from app.etl import pipeline
from app.etl.rules import EQUIVALENCE, parse_rules
from app.spark.loader import load_dataset, resolve_path, sniff_csv
from conftest import DATA

LAPTOP = "equivalencia: producto: Laptop Pro14 -> Laptop Pro 14"


def run_etl(spark, rules, answer=None):
    """ETL of equivalencias.csv. answer: reply to equivalence questions (None: none may be asked)."""
    path = resolve_path(str(DATA / "equivalencias.csv"))
    load = load_dataset(spark, path, "csv", sniff_csv(path))
    asked = []

    def decide(d):
        asked.append(d.key)
        if d.key.startswith("equivalence:") and answer is not None:
            return answer
        return {"transform": "all", "unknown": True, "duplicates": True}[d.key]
    report = pipeline.run(spark, load, parse_rules(rules), decide)
    units = {r[0]: r[1] for r in report.df.groupBy("producto").sum("cantidad").collect()}   # 5 groups
    return report, units, asked


def test_syntax_of_an_equivalence():
    rule = parse_rules("equivalencia: product: 'Laptop Pro14' -> Laptop Pro 14")[0]
    assert (rule.kind, rule.name, rule.values) == (EQUIVALENCE, "product", ("Laptop Pro14", "Laptop Pro 14"))
    assert rule.to_dict()["de"] == "Laptop Pro14" and rule.to_dict()["tipo"] == "equivalencia"
    with pytest.raises(AppError, match="Equivalencia no valida"):
        parse_rules("equivalencia: product: Mouse -> Mouse")


def test_similar_values_are_reported_but_never_merged_without_authorization(spark):
    report, units, asked = run_etl(spark, "")
    assert units == {"Laptop Pro 14": 5, "Laptop Pro14": 1, "Mouse X": 1, "MouseX": 4, "Mouse": 2}
    assert not any(k.startswith("equivalence:") for k in asked)
    assert report.similar_values == ["producto: 'Laptop Pro 14' (2) / 'Laptop Pro14' (1)",
                                     "producto: 'Mouse X' (1) / 'MouseX' (1)"]
    assert report.to_dict()["posibles_equivalencias_no_aplicadas"] == report.similar_values


def test_confirmed_equivalence_is_applied_and_recorded(spark):
    report, units, asked = run_etl(spark, LAPTOP, answer=True)
    assert asked.count("equivalence:producto:Laptop Pro14") == 1
    assert units == {"Laptop Pro 14": 6, "Mouse X": 1, "MouseX": 4, "Mouse": 2}      # MouseX: not authorized
    applied = [t.to_dict() for t in report.transformations if t.kind == "equivalence"]
    assert applied == [{"tipo": "equivalence", "columna": "producto",
                        "descripcion": "producto: equivalencia confirmada 'Laptop Pro14' -> 'Laptop Pro 14'",
                        "valores_afectados": 1,
                        "detalle": ["producto: 'Laptop Pro14' (1 registro(s)) -> 'Laptop Pro 14' (2 registro(s))"],
                        "regla": "equivalencia de la configuracion del caso, confirmada por el usuario",
                        "de": "Laptop Pro14", "a": "Laptop Pro 14"}]
    data = json.loads(json.dumps(report.to_dict(), default=str))
    assert data["decisiones"]["equivalence:producto:Laptop Pro14"] is True
    assert {r["tipo"] for r in data["reglas"]} == {"equivalencia"}
    assert sorted(r[0] for r in report.df.select("referencia").collect()) == [f"R-00{i}" for i in range(1, 7)]  # 6 rows


def test_unconfirmed_equivalence_is_not_applied(spark):
    report, units, _ = run_etl(spark, LAPTOP, answer=False)
    assert units["Laptop Pro14"] == 1 and units["Laptop Pro 14"] == 5
    assert not any(t.kind == "equivalence" for t in report.transformations)
    assert report.decisions["equivalence:producto:Laptop Pro14"] is False
    assert "Equivalencia no confirmada (no aplicada): producto: 'Laptop Pro14' -> 'Laptop Pro 14'." in report.warnings


def test_value_not_in_the_data_asks_nothing(spark):
    report, units, asked = run_etl(spark, "equivalencia: producto: Tablet -> Tablet 10")
    assert not any(k.startswith("equivalence:") for k in asked) and units["Laptop Pro14"] == 1
    assert "Equivalencia sin efecto: 'Tablet' no aparece en producto." in report.warnings


@pytest.mark.parametrize("rule, message", [
    ("equivalencia: codigo_ref: 1 -> 2", "no es una columna de texto descriptivo"),      # numeric and named 'codigo'
    ("equivalencia: referencia: R-001 -> R-002", "valor distinto en casi cada registro"),   # a code column
    ("equivalencia: cantidad: 1 -> 2", "no es una columna de texto descriptivo"),
    ("equivalencia: color: rojo -> Rojo", "no existe"),
])
def test_identifiers_codes_and_numbers_are_never_changed(spark, rule, message):
    with pytest.raises(AppError, match=message):
        run_etl(spark, rule, answer=True)


# --- the exam ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def reference(spark):
    df = ex.reference_clean(spark).cache()
    df.count()
    yield df
    df.unpersist()


@pytest.fixture(scope="module")
def exam_with_equivalence(spark):
    return ex.analyzer_etl(spark, ex.RULES + ex.EQUIVALENCES, confirm_equivalences=True)


def test_exam_difference_exists_without_the_equivalence(spark, reference):
    session = ex.analyzer_etl(spark)                       # reference rules only: as in phase 5
    _, _, got = ex.ask_exam(session, ex.QUESTIONS[0][0])
    expected = ex.as_result(ex.run_reference(spark, reference, ex.QUERIES[0]))
    assert got["Laptop Pro14"] == 11 and expected["Laptop Pro 14"] - got["Laptop Pro 14"] == 11
    assert any("'Laptop Pro14' (14)" in line for line in session.etl.similar_values)   # reported, not merged


def test_exam_unconfirmed_equivalence_keeps_the_difference(spark):
    session = ex.analyzer_etl(spark, ex.RULES + ex.EQUIVALENCES, confirm_equivalences=False)
    _, _, got = ex.ask_exam(session, ex.QUESTIONS[0][0])
    assert got["Laptop Pro14"] == 11


@pytest.mark.parametrize("n", range(10))
def test_exam_ten_questions_with_the_confirmed_equivalence(spark, reference, exam_with_equivalence, n):
    expected = ex.as_result(ex.run_reference(spark, reference, ex.QUERIES[n]))
    ev, _, got = ex.ask_exam(exam_with_equivalence, ex.QUESTIONS[n][0])
    assert ev is not None and ex.same(expected, got)


def test_exam_rows_and_evidence_with_the_equivalence(spark, reference, exam_with_equivalence):
    report, clean = exam_with_equivalence.etl, exam_with_equivalence.df
    assert report.valid_rows == 11758
    assert reference.select("order_id").exceptAll(clean.select("order_id")).count() == 0
    applied = [t for t in report.transformations if t.kind == "equivalence"]
    assert [(t.params["from"], t.params["to"], t.affected) for t in applied] == [("Laptop Pro14", "Laptop Pro 14", 14)]
    # Same product per order as the reference, for every order (not only the totals).
    joined = clean.alias("a").join(reference.alias("r"), "order_id")
    assert joined.where("a.product <> r.product").count() == 0
