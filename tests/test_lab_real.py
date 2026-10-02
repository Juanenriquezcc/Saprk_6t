"""Golden test against the REAL lab: real dataset + the options printed in the PDF.

The document's options are values of the real dataset, so this test needs both:

  tests/data/lab_real/dataset.csv   (or .json / .parquet)
  tests/data/lab_real/opciones.json

opciones.json format (one entry per question of the PDF, exactly as printed):

  {
    "1": {"question": "Cuantos registros contiene el dataset?",
          "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
          "expected": "B"},
    "6": {"question": "... son 15? V/F", "expected": "FALSO", "correct_value": "..."}
  }

The options exist only here (tests), never in app/. Skipped while the files are missing.
After a run, tests/data/lab_real/resultados.json holds, for every question: question,
intent, SQL, result, chosen option (or verdict) and the full evidence.
"""
import json
from pathlib import Path

import pytest

from conftest import chooser

REAL = Path(__file__).resolve().parent / "data" / "lab_real"
DATASET = next((p for p in sorted(REAL.glob("dataset.*")) if p.suffix in (".csv", ".json", ".parquet")), None) \
    if REAL.exists() else None
OPTIONS = REAL / "opciones.json"
pytestmark = pytest.mark.skipif(DATASET is None or not OPTIONS.exists(),
                                reason="faltan tests/data/lab_real/dataset.* y opciones.json (datos reales del taller)")


def cases():
    if not OPTIONS.exists():
        return []
    return sorted(json.loads(OPTIONS.read_text(encoding="utf-8")).items(), key=lambda kv: int(kv[0]))


RESULTS = REAL / "resultados.json"


@pytest.fixture(scope="module")
def real_session(spark):
    from app.session import LabSession
    from app.spark.loader import detect_format, load_dataset, sniff_csv

    fmt = detect_format(DATASET)
    load = load_dataset(spark, DATASET, fmt, sniff_csv(DATASET) if fmt == "csv" else None)
    session = LabSession.start(spark, load)
    yield session
    # For each question: question, intent, SQL, result, chosen option / verdict and full evidence.
    RESULTS.write_text(json.dumps([{
        "numero": e.number, "pregunta": e.question, "intent": e.intent, "sql": e.sql,
        "resultado": e.result_text, "valor": e.value, "opcion_elegida": e.matched_option,
        "veredicto": e.verdict, "respuesta": e.answer, "evidencia": e.to_dict(),
    } for e in session.history], ensure_ascii=False, indent=2), encoding="utf-8")
    load.df.unpersist()   # other tests re-register their own view through the `lab` fixture


@pytest.mark.parametrize("number, case", cases(), ids=[k for k, _ in cases()])
def test_real_lab_question(real_session, number, case):
    real_session.load.df.createOrReplaceTempView("dataset")
    text = f"{number}. {case['question']}"
    if case.get("options"):
        text += "\n" + "\n".join(f"{k}) {v}" for k, v in case["options"].items())
    ev = real_session.ask(text, chooser(case.get("choices", {})))
    if case.get("options"):
        assert ev.matched_option == case["expected"], (ev.result_text, ev.sql)
    else:
        assert ev.verdict == case["expected"], (ev.result_text, ev.sql)
        if case.get("correct_value") is not None:
            assert ev.correct_value == case["correct_value"]
