"""Recovery: one real menu session with every kind of error; after each one the
application keeps working and the next operation succeeds."""
from app.query import spec as s
from app.ui import menu

SCRIPT = [
    "3",                                            # lab mode
    "¿Cuál fue el precio máximo?", "", "",          #   ambiguous -> user cancels the choice
    "¿Qué hora es?", "",                            #   not recognized
    "¿Cuántos registros hay?", "",                  #   OK
    ":q",
    "8",                                            # Spark SQL
    "SELEC 1;",                                     #   typo
    "SELECT nope FROM dataset;",                    #   missing column
    "SELECT AVG(Company) FROM dataset;",            #   incompatible type (ANSI)
    "DROP VIEW dataset;",                           #   forbidden
    "SELECT COUNT(*) AS n FROM dataset;",           #   OK
    ":q",
    "9",                                            # guided analysis
    "8", "7", "1", "abc",                           #   invalid filter value (Volume > abc) -> retry
    "7", "1", "2000", "n",                          #   Volume > 2000
    "0",
    "2", "¿Cuál es el volumen total?", "", "",      # question from the menu + Enter
    "99",                                           # invalid menu option
    "0",
]


def test_session_recovers_after_every_error(lab, monkeypatch, capsys):
    session = lab("stocks_small.csv")
    answers = iter(SCRIPT)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    menu.run(session)                       # returns normally: nothing closed the application
    out = capsys.readouterr().out

    for message in ["Pregunta cancelada.",
                    "Pregunta no reconocida automaticamente.",
                    "La consulta debe empezar con SELECT",
                    "La columna 'nope' no existe en el dataset.",
                    "Solo se permiten consultas de lectura",
                    "Numero no valido o ambiguo: 'abc'",
                    "Opcion no valida."]:
        assert message in out, message
    assert ("Hay valores que no se pueden convertir" in out) or ("Tipos de datos incompatibles" in out)
    assert "Traceback" not in out

    # Only the successful operations produced evidence, each one with its correct value.
    assert [(e.intent, e.value) for e in session.history] == [
        (s.COUNT_ROWS, 12), (s.MANUAL_SQL, 12), (s.COUNT_WHERE, 4), (s.AGG_SCALAR, 22200)]
    assert next(answers, None) is None      # the whole script was consumed
    assert session.spark.sql("SELECT COUNT(*) FROM dataset").first()[0] == 12   # dataset intact
