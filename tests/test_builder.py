"""SQL builder: QuerySpec -> Spark SQL (string checks, plus execution on a tiny dataset)."""
import pytest

from app.query import spec as s
from app.query.builder import build_sql, build_tie_check, ident, literal
from app.query.spec import Condition, DateFilter, Metric, QuerySpec


def col(name):
    return Metric("column", (name,))


def test_identifier_quoting():
    assert ident("Close") == "Close"
    assert ident("Date") == "`Date`"            # reserved word
    assert ident("precio cierre") == "`precio cierre`"


def test_literals():
    assert literal(15) == "15"
    assert literal(15.0) == "15"
    assert literal(2.5) == "2.5D"
    assert literal("O'Hara") == "'O\\'Hara'"


def test_scalar_avg():
    sql = build_sql(QuerySpec(s.AGG_SCALAR, s.SCALAR, "AVG", col("Close"))).sql
    assert sql == "SELECT AVG(Close) AS avg_close\nFROM dataset"


def test_count_where_column_comparison():
    spec = QuerySpec(s.COUNT_WHERE, s.SCALAR, "COUNT", filters=[Condition("Close", ">", other_column="Open")])
    assert build_sql(spec).sql == "SELECT COUNT(*) AS total_registros\nFROM dataset\nWHERE Close > Open"


def test_group_top_has_deterministic_order_and_limit():
    spec = QuerySpec(s.GROUP_TOP, s.TOP, "SUM", col("Volume"), group_by="Company", order="DESC")
    sql = build_sql(spec).sql
    assert "GROUP BY Company" in sql
    assert "ORDER BY sum_volume DESC NULLS LAST, Company ASC" in sql
    assert sql.endswith("LIMIT 1")


def test_pct_change_uses_nullif():
    spec = QuerySpec(s.PCT_CHANGE, s.SCALAR, "AVG", Metric("pct_change", ("Open", "Close")))
    assert "NULLIF(Open, 0)" in build_sql(spec).sql


def test_period_change_uses_min_by_max_by():
    spec = QuerySpec(s.PCT_CHANGE_PERIOD, s.GROUP, "PERIOD_CHANGE", col("Close"), group_by="Company",
                     period_column="Date")
    sql = build_sql(spec).sql
    assert "max_by(Close, `Date`)" in sql and "min_by(Close, `Date`)" in sql and "NULLIF" in sql


def test_record_excludes_nulls_and_derived_value_is_selected():
    spec = QuerySpec(s.DIFFERENCE, s.RECORD, None, Metric("difference", ("High", "Low")), order="DESC")
    sql = build_sql(spec).sql
    assert sql.startswith("SELECT *, (High - Low) AS high_menos_low")
    assert "(High - Low) IS NOT NULL" in sql


def test_percentage_of_rows():
    spec = QuerySpec(s.PERCENTAGE_OF, s.SCALAR, "PERCENT",
                     percent_filters=[Condition("Close", ">", other_column="Open")])
    assert "COUNT_IF(Close > Open) / NULLIF(COUNT(*), 0)" in build_sql(spec).sql


def test_date_filters_for_text_dates_use_try_to_date():
    spec = QuerySpec(s.COUNT_WHERE, s.SCALAR, "COUNT",
                     date_filters=[DateFilter("fecha", ">=", "2024-01-01", "text", "dd/MM/yyyy")])
    assert "try_to_date(fecha, 'dd/MM/yyyy') >= DATE'2024-01-01'" in build_sql(spec).sql


def test_in_filter_and_having():
    spec = QuerySpec(s.GROUP_AGG, s.GROUP, "SUM", col("Volume"), group_by="Company",
                     filters=[Condition("Company", "IN", value=["AAPL", "MSFT"])],
                     having=[Condition("value", ">", value=1000)])
    sql = build_sql(spec).sql
    assert "Company IN ('AAPL', 'MSFT')" in sql and "HAVING sum_volume > 1000" in sql


def test_spec_reports_columns_and_filters():
    spec = QuerySpec(s.AGG_WHERE, s.SCALAR, "SUM", col("Volume"), filters=[Condition("Company", "=", value="AAPL")])
    assert spec.columns_used() == ["Volume", "Company"]
    assert spec.filter_labels() == ["Company = AAPL"]
    assert spec.to_dict()["intent"] == s.AGG_WHERE


def test_every_shape_runs_on_spark(lab):
    """All generated SQL must be valid Spark SQL with ANSI on."""
    session = lab("stocks_small.csv")
    specs = [
        QuerySpec(s.AGG_SCALAR, s.SCALAR, "AVG", col("Close")),
        QuerySpec(s.COUNT_DISTINCT, s.SCALAR, "COUNT_DISTINCT", col("Company")),
        QuerySpec(s.GROUP_AGG, s.GROUP, "SUM", col("Volume"), group_by="Company",
                  having=[Condition("value", ">", value=1000)]),
        QuerySpec(s.GROUP_TOP, s.TOP, "AVG", col("Close"), group_by="Company", order="ASC"),
        QuerySpec(s.RECORD_EXTREME, s.RECORD, None, col("Volume"), order="DESC"),
        QuerySpec(s.DIFFERENCE, s.RECORD, None, Metric("difference", ("High", "Low")), order="DESC"),
        QuerySpec(s.PCT_CHANGE, s.SCALAR, "AVG", Metric("pct_change", ("Open", "Close"))),
        QuerySpec(s.PCT_CHANGE_PERIOD, s.SCALAR, "PERIOD_CHANGE", col("Close"), period_column="Date"),
        QuerySpec(s.PERCENTAGE_OF, s.SCALAR, "PERCENT", col("Volume"),
                  percent_filters=[Condition("Company", "=", value="AAPL")]),
        QuerySpec(s.COLUMN_COMPARISON, s.COMPARE, "AVG", comparison=("Open", "Close")),
        QuerySpec(s.COUNT_WHERE, s.SCALAR, "COUNT", date_filters=[DateFilter("Date", ">=", "2024-01-04")]),
    ]
    for spec in specs:
        built = build_sql(spec)
        session.spark.sql(built.sql).collect()
        if spec.shape in (s.TOP, s.RECORD):
            session.spark.sql(build_tie_check(spec, built, 1.5)).collect()
