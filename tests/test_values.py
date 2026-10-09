"""Phase 4.1: values named in a question are filters, asked when ambiguous, never dropped silently.

valores/clientes.csv (active dataset), by hand:
  id cliente      ciudad    cupo  referido_por
  1  Ana María    Bogotá    1000  Luis
  2  José Pérez   Medellín  2000
  3  Luis         Bogotá    1500  Ana María
  4  Ángela Ruiz  Cali       500
  5  Sofía        Pasto      800  Luis
  6  Marta        Bogotá    1200  José Pérez
  'cliente' has a different value on every row: an identifier-like column, its values are indexed
  (before phase 4.1 they were not, and 'Ana' was silently ignored). 'ciudad' and 'referido_por'
  are categories. Total cupo 7000; Bogotá 1000+1500+1200 = 3700; referred by Luis 1000+800 = 1800.
Catalog: the fixtures of test_joins.py (Ana: pedidos 101 and 102, 1+3 units, 3000+150 amount).
"""
import time
from pathlib import Path

from app.query import spec as s
from app.questions.intents import NeedsInput, proper_phrases
from app.questions.parser import parse_question
from app.session import LabSession
from app.spark.loader import LoadResult
from conftest import chooser
from test_joins import CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS, ask, confirm, refused, rows, shop, spec_of  # noqa: F401

CLIENTS = "valores/clientes.csv"


def interpret(session, text, answers=None):
    return session.interpreter.interpret(parse_question(text), answers or {})


def filters(spec):
    return [(f.column, f.op, f.value) for f in spec.filters]


# --- single table ---------------------------------------------------------------------------------

def test_a_name_of_a_unique_column_is_a_filter(lab):
    session = lab(CLIENTS)
    column = session.profile.column("cliente")
    assert column.is_id and not column.is_categorical and column.values_complete and "Ana María" in column.values
    for question in ("¿Cuál es el cupo total de Ángela Ruiz?", "¿Cuál es el cupo total de ángela ruiz?",
                     "¿Cuál es el cupo total de ANGELA RUIZ?"):          # case and accents are normalized
        spec = interpret(session, question)
        assert (spec.intent, spec.aggregation, filters(spec)) == (s.AGG_WHERE, "SUM", [("cliente", "=", "Ángela Ruiz")])
        ev = ask(session, question)
        assert ev.value == 500 and "WHERE cliente = 'Ángela Ruiz'" in ev.sql
    assert session.interpreter.value_lookup.queries == 0         # found in memory: no Spark search


def test_a_value_that_does_not_exist_is_never_dropped(lab):
    session = lab(CLIENTS)
    question = "¿Cuál es el cupo total de Andrea?"
    need = interpret(session, question)
    assert need.key == "unknown:andrea" and "'Andrea' no coincide con ningun valor" in need.message
    assert [value for _, value in need.options] == ["ignore"]     # the only way on is the user's decision
    ev = session.solve_question(question, lambda need: None)    # cancelled: no answer computed
    assert ev.validation == "NO RESUELTA" and ev.sql == ""
    assert ask(session, question, {"unknown:andrea": "ignore"}).value == 7000


def test_category_values_are_filters(lab):
    session = lab(CLIENTS)
    for question in ("¿Cuál es el cupo total de los clientes de Bogotá?", "¿Cuál es el cupo total de los clientes de bogota?"):
        ev = ask(session, question)
        assert ev.filters == ["ciudad = Bogotá"] and ev.value == 3700 and "WHERE ciudad = 'Bogotá'" in ev.sql


def test_a_value_in_several_columns_is_asked(lab):
    session = lab(CLIENTS)
    question = "¿Cuál es el cupo total de Luis?"
    need = interpret(session, question)
    assert need.key == "value:luis" and set(v for _, v in need.options) == {("cliente", "Luis"), ("referido_por", "Luis")}
    ev = ask(session, question, {"value:luis": ("cliente", "Luis")})
    assert ev.value == 1500 and "WHERE cliente = 'Luis'" in ev.sql
    ev = ask(session, question, {"value:luis": ("referido_por", "Luis")})
    assert ev.value == 1800 and "WHERE referido_por = 'Luis'" in ev.sql


def test_ordinary_words_never_become_filters(lab):
    session = lab(CLIENTS)
    for question in ("¿Cuál es el cupo total registrado?", "¿Cuál es el cupo Total?"):   # chooser({}) fails on any question
        ev = ask(session, question)
        assert ev.filters == [] and ev.value == 7000
    ev = ask(session, "Cupo total por ciudad")               # capitalized first word: not a value
    assert rows(ev) == {"Bogotá": 3700, "Medellín": 2000, "Pasto": 800, "Cali": 500}
    # 'Ciudad' is an ordinary word (a role alias): only 'Gótica' is left unexplained.
    assert proper_phrases("¿Cuántos pedidos hizo Ana María en Ciudad Gótica?", []) == ["Ana María", "Gótica"]
    assert proper_phrases("Ana compró. Luego Total y Cuál", []) == []


# --- catalog -------------------------------------------------------------------------------------

def test_values_in_catalog_questions(shop):
    confirm(shop, CLIENTES_PEDIDOS, PEDIDOS_PRODUCTOS)
    for question in ("¿Cuántos pedidos hizo Ana?", "¿Cuántos pedidos hizo ana?"):          # before: 7 (all orders)
        spec = spec_of(shop, question)
        assert filters(spec) == [("clientes.cliente", "=", "Ana")] and spec.joins[0].table == "clientes"
        ev = ask(shop, question)
        assert ev.value == 2 and "WHERE clientes.cliente = 'Ana'" in ev.sql
    assert ask(shop, "¿Cuántas unidades compró ANA?").value == 4
    assert ask(shop, "¿Cuál es el total de importe de Ana?").value == 3150
    need = spec_of(shop, "¿Cuántos pedidos hizo Andrea?")
    assert need.key == "unknown:andrea"
    # A value of a column present in two tables: which table is asked (pedidos 101 + 103 = 3 units).
    need = spec_of(shop, "¿Cuántas unidades de Laptop se vendieron?")
    assert need.key == "table:producto" and [v for _, v in need.options] == ["pedidos", "productos", "promociones"]
    assert ask(shop, "¿Cuántas unidades de Laptop se vendieron?", {"table:producto": "pedidos"}).value == 3
    ev = ask(shop, "¿Qué clientes de Cali no tienen pedidos?")
    assert [r[1] for r in ev.result_rows] == ["Pedro"] and "WHERE clientes.ciudad = 'Cali'" in ev.sql
    refused(spec_of(shop, "¿Cuántos pedidos hizo Ana con unidades?"), "Pregunta no reconocida")


# --- large data ------------------------------------------------------------------------------------

def test_large_column_is_searched_once_with_spark_not_collected(spark):
    rows_n = 300_000            # 150 000 distinct clients: far above the in-memory value index
    df = spark.range(rows_n).selectExpr("id AS pedido_id", "concat('Cli', CAST(id % 150000 AS STRING)) AS cliente",
                                        "CAST(id % 5 AS INT) AS unidades")
    df.createOrReplaceTempView("dataset")
    load = LoadResult(df=df, path=Path("sintetico_grande.csv"), fmt="csv", rows=rows_n, columns=df.columns)
    session = LabSession.start(spark, load)
    column = session.profile.column("cliente")
    assert column.values == [] and not column.values_complete              # never brought to Python
    lookup = session.interpreter.value_lookup
    start = time.perf_counter()
    ev = session.ask("¿Cuántos pedidos hizo Cli777?", chooser({}))
    first = time.perf_counter() - start
    assert ev.filters == ["cliente = Cli777"] and ev.value == 2              # ids 777 and 150777
    assert session.ask("¿Cuántas unidades compró Cli777?", chooser({})).value == 4   # 777 % 5 + 150777 % 5
    assert lookup.queries == 1 and list(lookup.cache) == ["cli777"]         # one Spark search, then cached
    need = session.interpreter.interpret(parse_question("¿Cuántos pedidos hizo Cli999999?"))
    assert isinstance(need, NeedsInput) and need.key == "unknown:cli999999" and lookup.queries == 2
    assert first < 60                                                       # generous bound on a lab PC
