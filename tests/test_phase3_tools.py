"""Phase 3: manual Spark SQL, guided analysis and full analysis (all reuse QuerySpec/Evidence)."""
import pytest

import config
from app.analysis import full, guided
from app.errors import AppError
from app.query import spec as s
from app.query.builder import build_sql
from app.query.spec import Condition


# --- manual Spark SQL -------------------------------------------------------------

@pytest.mark.parametrize("sql, columns, rows", [
    ("SELECT COUNT(*) FROM dataset;", ["count(1)"], [[12]]),
    ("SELECT * FROM dataset LIMIT 10", ["Date", "Company", "Open", "High", "Low", "Close", "Volume"], None),
    ("SELECT Company, AVG(Close)\nFROM dataset\nGROUP BY Company\nORDER BY Company;", ["Company", "avg(Close)"],
     [["AAPL", 103.0], ["MSFT", 303.5], ["NVDA", 55.625]]),
    ("with t as (select Volume from dataset) select max(Volume) as m from t", ["m"], [[4000]]),
])
def test_manual_sql_valid(lab, sql, columns, rows):
    session = lab("stocks_small.csv")
    ev = session.run_sql(sql)
    assert ev.intent == s.MANUAL_SQL and ev.result_columns == columns
    assert len(ev.result_types) == len(columns)
    if rows is not None:
        assert ev.result_rows == rows
    assert session.history[-1] is ev and ev.number == 1
    assert not ev.sql.endswith(";")


def test_manual_sql_types_and_single_value(lab):
    ev = lab("stocks_small.csv").run_sql("SELECT COUNT(*) AS n, MAX(Date) AS d FROM dataset")
    assert ev.result_types == ["bigint", "date"]
    ev = lab("stocks_small.csv").run_sql("SELECT COUNT(*) AS n FROM dataset")
    assert ev.result_text == "n = 12" and ev.value == 12


def test_manual_sql_result_is_bounded(lab, monkeypatch):
    monkeypatch.setattr(config, "MANUAL_SQL_MAX_ROWS", 5)
    ev = lab("stocks_small.csv").run_sql("SELECT * FROM dataset")
    assert len(ev.result_rows) == 5
    assert ev.result_text == "5 fila(s) mostrada(s) (hay mas filas)"
    assert any("primeras 5 filas" in w for w in ev.warnings)


@pytest.mark.parametrize("sql, message", [
    ("SELEC * FROM dataset", "debe empezar con SELECT"),
    ("SELECT nope FROM dataset", "La columna 'nope' no existe"),
    ("SELECT * FROM otra_tabla", "no existe"),
    ("SELECT Company, AVG(Close) FROM dataset", "GROUP BY"),
    ("SELECT * FROM dataset WHERE Close >", "error de sintaxis"),
    ("", "vacia"),
    ("SELECT 1; SELECT 2", "una sola consulta"),
])
def test_manual_sql_invalid_gives_clear_errors(lab, sql, message):
    with pytest.raises(AppError, match=message):
        lab("stocks_small.csv").run_sql(sql)


@pytest.mark.parametrize("sql", [
    "DROP VIEW dataset",
    "CREATE OR REPLACE TEMP VIEW dataset AS SELECT 1 AS x",
    "INSERT INTO dataset VALUES (1)",
    "CACHE TABLE dataset",
    "SET spark.sql.ansi.enabled=false",
    "with t as (select 1) insert into x select * from t",
])
def test_manual_sql_cannot_modify_dataset_or_session(lab, sql):
    session = lab("stocks_small.csv")
    with pytest.raises(AppError, match="Solo se permiten consultas de lectura"):
        session.run_sql(sql)
    assert session.run_sql("SELECT COUNT(*) AS n FROM dataset").value == 12
    assert session.spark.conf.get("spark.sql.ansi.enabled") == "true"


def test_unconverted_java_error_is_still_translated():
    from app.query.executor import translate_error

    class FakeJavaError(Exception):
        pass

    java = FakeJavaError("An error occurred while calling o43.sql.\n: org.apache.spark.sql.AnalysisException: "
                         "[TABLE_OR_VIEW_NOT_FOUND] The table or view `x` cannot be found.")
    err = translate_error(java)
    assert err.message.startswith("La tabla o vista no existe: [TABLE_OR_VIEW_NOT_FOUND]")
    generic = translate_error(FakeJavaError("An error occurred while calling o1.sql.\n"
                                            ": java.lang.IllegalStateException: algo raro"))
    assert generic.message == "Spark no pudo ejecutar la consulta: algo raro"


def test_unconverted_java_error_is_retried_once():
    from app.query.executor import execute

    class Py4JJavaError(Exception):
        pass

    class FakeDF:
        columns, dtypes = ["n"], [("n", "int")]

        def limit(self, n):
            return self

        def collect(self):
            return [(1,)]

    class FakeSpark:
        def __init__(self, failures):
            self.failures, self.calls = failures, 0

        def sql(self, sql):
            self.calls += 1
            if self.calls <= self.failures:
                raise Py4JJavaError("An error occurred while calling o43.sql.\n: org.apache.spark.sql."
                                    "AnalysisException: [TABLE_OR_VIEW_NOT_FOUND] The table or view `t` cannot be found.")
            return FakeDF()

    once = FakeSpark(failures=1)
    assert execute(once, "SELECT 1").rows == [(1,)] and once.calls == 2
    with pytest.raises(AppError, match="La tabla o vista no existe"):
        execute(FakeSpark(failures=5), "SELECT 1")


def test_spark_never_writes_into_the_project_folder(lab):
    session = lab("stocks_small.csv")
    session.run_sql("DESCRIBE dataset")
    session.run_sql("SHOW TABLES")
    root = config.PROJECT_ROOT
    assert not (root / "spark-warehouse").exists() and not (root / "metastore_db").exists()
    assert session.spark.conf.get("spark.sql.warehouse.dir").startswith("file:")


def test_manual_sql_allows_words_inside_literals(lab):
    ev = lab("stocks_small.csv").run_sql("SELECT COUNT(*) AS n FROM dataset WHERE Company <> 'DROP; TABLE'")
    assert ev.value == 12


# --- guided analysis (each option builds a QuerySpec for the shared builder) ---------

def test_guided_specs_reuse_the_builder(lab):
    session = lab("stocks_small.csv")
    p = session.profile
    cond, _ = guided.condition(p, "Close", ">", "Open")
    specs = {
        "stats": guided.column_stats(p, "Close"),
        "group": guided.group("Company", "AVG", "Close"),
        "filter": guided.filter_rows([cond], limit=3),
        "ranking": guided.ranking("Company", "SUM", "Volume", "DESC", 2),
        "extreme_value": guided.extreme("Volume", "MIN", "value"),
        "extreme_record": guided.extreme("Volume", "MAX", "record"),
        "compare": guided.compare("Open", "Close", "AVG"),
        "pct": guided.pct_change("Open", "Close", "AVG"),
        "pct_group": guided.pct_change("Open", "Close", "AVG", "Company", "DESC", 1),
        "count": guided.count_where([cond]),
    }
    results = {name: session.run_spec(spec, f"Analisis guiado: {name}") for name, spec in specs.items()}
    for name, ev in results.items():
        assert ev.sql == build_sql(specs[name]).sql, name     # same builder, no separate SQL
        assert ev.spec["intent"] == specs[name].intent
    assert results["stats"].result_columns == ["columna"] + guided.NUMERIC_STATS
    assert results["stats"].result_rows[0][:6] == ["Close", 12, 0, 12, 52.0, 318.0]
    assert results["group"].result_rows == [["MSFT", 303.5], ["AAPL", 103.0], ["NVDA", 55.625]]
    assert len(results["filter"].result_rows) == 3 and results["filter"].sql.endswith("LIMIT 3")
    assert results["ranking"].result_rows == [["NVDA", 13000], ["AAPL", 5700]]
    assert results["extreme_value"].value == 700
    assert results["extreme_record"].result_rows[0][1] == "NVDA"
    assert results["compare"].value == "Close"
    assert results["pct"].value == pytest.approx(2.4453731)
    assert results["pct_group"].value == "NVDA"
    assert results["count"].value == 8
    assert [e.number for e in session.history] == list(range(1, 11))


def test_guided_stats_of_text_column(lab):
    session = lab("ventas.csv")
    ev = session.run_spec(guided.column_stats(session.profile, "categoria"), "stats")
    assert ev.result_columns == ["columna"] + guided.OTHER_STATS
    assert ev.result_rows == [["categoria", 6, 0, 2, "Bebidas", "Panaderia"]]


def test_guided_conditions_are_typed(lab):
    p = lab("ventas.csv").profile
    assert guided.condition(p, "cantidad", ">=", "3") == (Condition("cantidad", ">=", value=3), None)
    assert guided.condition(p, "categoria", "=", "bebidas")[0].value == "Bebidas"     # stored spelling
    assert guided.condition(p, "precio_total", ">", "precio_unitario")[0].other_column == "precio_unitario"
    cond, date_cond = guided.condition(p, "fecha", ">=", "2024-02-01")
    assert cond is None and date_cond.label() == "fecha >= 2024-02-01"
    for bad in [("fecha", "=", "01/02/2024"), ("cantidad", ">", "1.000"), ("cantidad", "~", "1")]:
        with pytest.raises(AppError):
            guided.condition(p, *bad)


def test_guided_menu_flow(lab, monkeypatch, capsys):
    """Drives the real guided menu with typed input: ranking, then count, then back."""
    from app.ui import guided as guided_ui
    session = lab("stocks_small.csv")
    answers = iter([
        "4", "1", "2", "1", "1", "3",        # ranking: Company, SUM, Open, mayor primero, top 3
        "8", "6", "1", "Open", "n",          # conteo: Close > Open
        "x",                                 # invalid option: keeps running
        "0",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    guided_ui.run(session)
    out = capsys.readouterr().out
    assert "Opcion no valida." in out
    ranking, count = session.history
    assert ranking.intent == s.GROUP_TOP and ranking.result_rows[0] == ["MSFT", 1196.0]
    assert count.intent == s.COUNT_WHERE and count.value == 8


# --- full analysis -----------------------------------------------------------------

@pytest.mark.parametrize("dataset", ["stocks_small.csv", "ventas.csv", "events.jsonl", "problem_columns.csv"])
def test_full_analysis_on_any_dataset(lab, dataset):
    session = lab(dataset)
    seen = []
    results = full.run(session, progress=lambda i, n, label: seen.append((i, n, label)))
    assert 1 <= len(results) <= config.FULL_ANALYSIS_MAX_STEPS
    assert [i for i, _, _ in seen] == list(range(1, len(results) + 1))
    assert all(r.error is None for r in results), [r.error.message for r in results if r.error]
    assert session.history == [r.evidence for r in results]     # every step produced Evidence
    for r in results:
        ev = r.evidence
        assert ev.sql and ev.question.startswith("Analisis completo:")
        if r.step.spec is not None:
            assert ev.sql == build_sql(r.step.spec).sql          # shared builder
        else:
            assert ev.intent == s.MANUAL_SQL and ev.sql.startswith("DESCRIBE")
    assert results[0].evidence.value == session.profile.rows


def test_full_analysis_uses_semantic_relations(lab):
    labels = [r.step.label for r in full.run(lab("stocks_small.csv"))]
    assert labels[:5] == ["Conteo de registros", "Columnas y tipos", "Nulos por columna",
                          "Perfil numerico (min, max, promedio, desviacion)", "Cardinalidad de columnas no numericas"]
    assert "Registros con Close > Open" in labels and "Mayor rango High - Low" in labels
    assert "Ranking: Company con mayor suma de Volume" in labels


def test_full_analysis_respects_the_limit(lab, monkeypatch):
    monkeypatch.setattr(config, "FULL_ANALYSIS_MAX_STEPS", 4)
    assert len(full.run(lab("stocks_small.csv"))) == 4


def test_full_analysis_continues_after_an_error(lab, monkeypatch):
    session = lab("stocks_small.csv")
    original = session.run_spec
    calls = {"n": 0}

    def flaky(spec, label):
        calls["n"] += 1
        if calls["n"] == 2:
            raise AppError("fallo simulado", "pista")
        return original(spec, label)

    monkeypatch.setattr(session, "run_spec", flaky)
    results = full.run(session)
    errors = [r for r in results if r.error]
    assert len(errors) == 1 and errors[0].error.message == "fallo simulado"
    assert len(results) == config.FULL_ANALYSIS_MAX_STEPS        # the rest still ran
