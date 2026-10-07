"""Workshop: validation of the student's answer, history and the 6-file export.

stocks_small.csv (12 rows): total volume NVDA 13000 > MSFT > AAPL; Close > Open in 8 rows.
"""
import csv
import json

from app.export import export_workshop, workshop_summary
from app.query import spec as s
from app.questions import validator as v
from conftest import chooser

MC = "¿Qué empresa tiene el mayor volumen total?\nA) AAPL\nB) MSFT\nC) NVDA\nD) TSLA"


def solve(session, text, selected=None, kind=None):
    return session.solve_question(text, chooser({}), kind=kind, selected=selected)


def test_multiple_choice_validation(lab):
    session = lab("stocks_small.csv")
    wrong = solve(session, MC, "B")
    assert wrong.validation == v.INCORRECT
    assert (wrong.correct_answer, wrong.selected_answer) == ("C) NVDA", "B) MSFT")
    assert "La opcion seleccionada fue B) MSFT" in wrong.validation_note
    assert solve(session, MC, "c)").validation == v.CORRECT
    assert solve(session, MC).validation == v.NO_ANSWER


def test_no_matching_option_is_not_forced(lab):
    ev = solve(lab("stocks_small.csv"), "¿Cuántos registros hay?\nA) 10\nB) 11\nC) 13\nD) 14", "A")
    assert ev.validation == v.UNDETERMINED and ev.correct_answer is None


def test_true_false_validation(lab):
    session = lab("stocks_small.csv")
    ev = solve(session, "Los registros donde Close > Open son 15.", "F")
    assert ev.verdict == "FALSO" and ev.validation == v.CORRECT and ev.correct_value == "8"
    assert solve(session, "Los registros donde Close > Open son 15.", "verdadero").validation == v.INCORRECT


def test_open_question_validation(lab):
    session = lab("stocks_small.csv")
    assert solve(session, "¿Cuántos registros hay?", "12").validation == v.CORRECT
    assert solve(session, "¿Cuántos registros hay?", "13").validation == v.INCORRECT
    assert solve(session, "¿Qué empresa tiene el mayor volumen total?", "nvda").validation == v.CORRECT


def test_type_chosen_by_user(lab):
    session = lab("stocks_small.csv")
    ev = solve(session, "¿Cuántos registros tienen Close > Open?", kind=s.TRUE_FALSE)
    assert ev.question_type == s.TRUE_FALSE and ev.validation == v.UNRESOLVED
    assert any("valor afirmado" in w for w in ev.warnings)
    ev = solve(session, "El volumen total es 22200", kind=s.OPEN)    # read as a question, not as a claim
    assert ev.question_type == s.OPEN and ev.value == 22200 and ev.verdict is None


def test_unresolved_questions_are_recorded(lab):
    session = lab("stocks_small.csv")
    ev = solve(session, "¿Qué hora es?", "A")
    assert ev.validation == v.UNRESOLVED and ev.sql == "" and session.questions == [ev]
    session.history.clear()


def test_export_workshop(lab, tmp_path):
    session = lab("stocks_small.csv")
    session.sql_errors = 0
    solve(session, "¿Cuántos registros hay?", "12")
    solve(session, MC, "B")
    solve(session, "Los registros donde Close > Open son 15.", "F")
    solve(session, "¿Qué hora es?")
    session.run_sql("SELECT COUNT(*) AS n FROM dataset")

    summary = workshop_summary(session)
    assert (summary["preguntas"], summary["correctas"], summary["incorrectas"], summary["no_resueltas"]) == (4, 2, 1, 1)
    assert summary["errores_sql"] == 0 and summary["consultas_ejecutadas"] >= 4

    folder = export_workshop(session, tmp_path)
    assert folder.name.startswith("taller_")
    names = {p.name for p in folder.iterdir()}
    assert names == {"taller.sql", "respuestas.csv", "evidencia.json", "evidencia.txt", "resumen.txt", "etl_report.json"}

    with (folder / "respuestas.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["estado"] for r in rows] == ["CORRECTA", "INCORRECTA", "CORRECTA", "NO RESUELTA"]
    assert rows[1]["respuesta_correcta"] == "C) NVDA" and rows[1]["respuesta_seleccionada"] == "B) MSFT"
    assert rows[1]["tipo"] == "Seleccion multiple"

    sql = (folder / "taller.sql").read_text(encoding="utf-8-sig")
    assert "WHERE Close > Open;" in sql and "SELECT COUNT(*) AS n FROM dataset;" in sql
    data = json.loads((folder / "evidencia.json").read_text(encoding="utf-8"))
    assert len(data["preguntas"]) == 4 and data["preguntas"][1]["validation"] == "INCORRECTA"
    text = (folder / "evidencia.txt").read_text(encoding="utf-8-sig")
    assert "Respuesta seleccionada: B) MSFT" in text and "Validacion: INCORRECTA" in text
    assert "Incorrectas:                1" in (folder / "resumen.txt").read_text(encoding="utf-8-sig")


MENU_SCRIPT = [
    "1",                                                   # Resolver pregunta
    "¿Cuántos registros hay?", "", "", "12", "",           #   open, type OK, answer 12, next
    MC, "", "", "B", "",                                   #   A-D pasted with the question, answer B
    "Los registros donde Close > Open son 15.", "", "", "F", "",   # true/false, answer F
    "¿Qué empresa tiene el mayor volumen total?", "", "2",  #   user says it is multiple choice
    "AAPL", "NVDA", "", "B", "0",                          #   types the options, answer B, back
    "99",                                                  # invalid option
    "2", "",                                               # list of questions
    "8", "", "",                                           # finish: summary + export
    "0",                                                   # exit (already exported: no question)
]


def test_workshop_menu_flow(lab, monkeypatch, capsys, tmp_path):
    import config
    from app.ui import workshop

    session = lab("stocks_small.csv")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path)
    answers = iter(MENU_SCRIPT)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    workshop.run(session)
    out = capsys.readouterr().out
    assert next(answers, None) is None and "Traceback" not in out
    assert [e.validation for e in session.questions] == [v.CORRECT, v.INCORRECT, v.CORRECT, v.CORRECT]
    assert session.questions[3].options == [["A", "AAPL"], ["B", "NVDA"]]
    assert "VALIDACION: INCORRECTA" in out and "Opcion no valida." in out
    folder = next(tmp_path.glob("taller_*"))
    assert len(list(folder.iterdir())) == 6


# --- final audit: one spec for every type, edge answers, reusable SQL -----------------------------

def test_open_multiple_choice_and_true_false_share_the_spec(lab):
    from app.questions.parser import parse_question
    session = lab("stocks_small.csv")
    base = "¿Qué empresa tiene el mayor volumen total?"
    specs = [session.interpreter.interpret(parse_question(t))
             for t in (base, base + "\nA) AAPL\nB) NVDA", "NVDA es la empresa con mayor volumen total")]
    core = [{k: v for k, v in sp.to_dict().items() if k not in ("question_type", "options", "claim", "claim_op")}
            for sp in specs]
    assert core[0] == core[1] == core[2]


def test_edge_answers(lab):
    session = lab("stocks_small.csv")
    base = "¿Qué empresa tiene el mayor volumen total?"
    ev = solve(session, base + "\nA) AAPL\nB) NVDA", "Z")                  # not an option
    assert ev.validation == v.INCORRECT and "no es una de las opciones" in ev.validation_note
    ev = solve(session, base + "\nA) NVDA\nB) nvda", "A")                  # two options = same result
    assert ev.validation == v.UNDETERMINED and ev.correct_answer is None
    assert solve(session, base + "\nA) AAPL\nB) NVDA").validation == v.NO_ANSWER
    assert solve(session, base, "Nvda").validation == v.CORRECT            # equivalent text
    assert solve(session, base + "\nA) AAPL\nB) NVDÁ", "b").correct_answer == "B) NVDÁ"


def test_exported_sql_is_reusable(lab, tmp_path, spark):
    session = lab("stocks_small.csv")
    solve(session, "¿Cuántos registros hay?")
    solve(session, MC, "C")
    solve(session, "¿Qué hora es?")
    sql = (export_workshop(session, tmp_path) / "taller.sql").read_text(encoding="utf-8-sig")
    assert "-- Pregunta 1:" in sql and "-- Pregunta 2:" in sql and "-- Pregunta 3:" in sql
    statements = [s.strip() for s in sql.split(";")]
    runnable = ["\n".join(l for l in s.splitlines() if not l.startswith("--")).strip() for s in statements]
    runnable = [s for s in runnable if s]
    assert len(runnable) == 3                       # COUNT, TOP and its tie check (Q3 has no SQL)
    for statement in runnable:
        spark.sql(statement).collect()              # every statement runs again as written
