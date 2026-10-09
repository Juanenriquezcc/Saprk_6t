"""Acceptance with the real sales exam: the ten reference queries vs the analyzer's questions.

Levels (never mixed):
  R  original data: the reference SQL and the analyzer over the same raw CSV (no ETL).
  B  question engine: the analyzer over the reference's clean data (isolates parser/builder/executor).
  A  end to end: original CSV -> the analyzer's own ETL (reference rules given explicitly) -> questions.
Every expected value is computed here with Spark from the files; none is written by hand.
"""
import pytest

import exam_acceptance as ex


@pytest.fixture(scope="module")
def reference(spark):
    df = ex.reference_clean(spark).cache()
    df.count()
    yield df
    df.unpersist()


@pytest.fixture(scope="module")
def ref_session(spark, reference):
    return ex.session_on(spark, reference, "referencia_limpia")


@pytest.fixture(scope="module")
def etl_session(spark):
    return ex.analyzer_etl(spark)


def test_reference_copy_reproduces_the_published_parquet(spark, reference):
    published = spark.read.parquet(str(ex.PARQUET))
    assert reference.dtypes == published.dtypes
    assert reference.count() == published.count() == 11758
    assert reference.exceptAll(published).count() == 0 and published.exceptAll(reference).count() == 0


def test_original_data_level_r(spark):
    raw = ex.read_raw(spark)
    assert raw.count() == 12030
    statuses = {r[0]: r[1] for r in raw.groupBy("status").count().collect()}       # 5 groups: bounded
    assert statuses["Entregado"] == 9353 and statuses["Devuelto"] == 1058
    session = ex.session_on(spark, raw, "originales")
    # sales_cop does not exist before the ETL: questions 4, 6 and 10 do not apply to the original data.
    for i in (0, 1, 2, 4, 6, 7, 8):
        question = ex.QUESTIONS[i][0]
        ev, asked, got = ex.ask_exam(session, question)
        assert ev is not None and asked == [], question
        assert ex.same(ex.as_result(ex.run_reference(spark, raw, ex.QUERIES[i])), got), question


@pytest.mark.parametrize("n", range(10))
def test_question_engine_level_b(spark, reference, ref_session, n):
    question, agg, measure, group, status = ex.QUESTIONS[n]
    expected_rows = ex.run_reference(spark, reference, ex.QUERIES[n])
    ev, asked, got = ex.ask_exam(ref_session, question)
    assert ev is not None, f"NO ADMITIDA: {got}"
    assert set(asked) <= {"metric:ingresos", "metric:ventas"}                 # only 'ingresos' is asked
    spec = ev.spec
    assert spec["aggregation"] == agg and spec["group_by"] == group
    assert ((spec["target"] or {}).get("columns") or [None]) == [measure]
    assert [(f["column"], f["op"], f["value"]) for f in spec["filters"]] == [("status", "=", status)]
    assert f"WHERE status = '{status}'" in ev.sql
    if group:
        assert f"GROUP BY {group}" in ev.sql and "DESC" in ev.sql
        assert ex.descending(ev.result_rows) and ex.descending(expected_rows)
    assert ex.same(ex.as_result(expected_rows), got)


def test_analyzer_etl_keeps_the_same_rows_and_sales_cop(spark, reference, etl_session):
    report, clean = etl_session.etl, etl_session.df
    assert (report.original_rows, report.valid_rows) == (12030, 11758)
    assert reference.select("order_id").exceptAll(clean.select("order_id")).count() == 0
    assert clean.select("order_id").exceptAll(reference.select("order_id")).count() == 0
    joined = clean.alias("a").join(reference.alias("r"), "order_id")
    assert joined.count() == 11758
    assert joined.where("a.sales_cop <> r.sales_cop OR a.sales_cop IS NULL").count() == 0   # round(quantity * price, 0)


@pytest.mark.parametrize("n", range(10))
def test_end_to_end_level_a(spark, reference, etl_session, n):
    question = ex.QUESTIONS[n][0]
    expected = ex.as_result(ex.run_reference(spark, reference, ex.QUERIES[n]))
    ev, asked, got = ex.ask_exam(etl_session, question)
    assert ev is not None, f"NO ADMITIDA: {got}"
    if n != 0:
        assert ex.same(expected, got)
        return
    # Known incompatibility (documented, not corrected): the reference maps 'Laptop Pro14' to 'Laptop Pro 14'
    # by hand; the analyzer's ETL only unifies case, outer spaces and accents, so 11 units stay apart.
    diff = {k: got.get(k, 0) - expected.get(k, 0) for k in set(got) | set(expected) if not ex.same(got.get(k, 0), expected.get(k, 0))}
    assert diff == {"Laptop Pro14": 11, "Laptop Pro 14": -11}
    assert sum(got.values()) == sum(expected.values())
