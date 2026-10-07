"""OLAP operations of the builder, run on Spark. Expected values by hand from ventas.csv:

  fecha       categoria  cantidad precio_unitario precio_total
  2024-01-10  Bebidas    3        2.5             7.5
  2024-01-15  Bebidas    1        1.75            1.75
  2024-02-03  Panaderia  10       0.5             5.0
  2024-02-20  Bebidas    2        2.5             5.0
  2024-03-05  Panaderia  1        12.0            12.0
  2024-03-18  Panaderia  6        0.5             3.0
"""
import pytest

from app.query import spec as s
from app.query.spec import Condition, Metric, QuerySpec


def run(session, spec):
    return session.run_spec(spec, "olap")


def total():
    return Metric("column", ("precio_total",))


def test_median_percentile_stddev(lab):
    session = lab("ventas.csv")
    assert run(session, QuerySpec(s.AGG_SCALAR, s.SCALAR, "MEDIAN", total())).value == 5
    ev = run(session, QuerySpec(s.AGG_SCALAR, s.SCALAR, "PERCENTILE", total(), percentile=0.9))
    assert ev.value == pytest.approx(9.75) and "percentile(precio_total, 0.9)" in ev.sql
    values = [7.5, 1.75, 5, 5, 12, 3]
    mean = sum(values) / 6
    expected = (sum((v - mean) ** 2 for v in values) / 5) ** 0.5
    assert run(session, QuerySpec(s.AGG_SCALAR, s.SCALAR, "STDDEV", total())).value == pytest.approx(expected)


def test_group_by_month_with_running_total(lab):
    ev = run(lab("ventas.csv"), QuerySpec(s.GROUP_AGG, s.GROUP, "SUM", total(), group_by="fecha",
                                          group_time="month", cumulative=True))
    assert ev.result_columns == ["mes", "sum_precio_total", "acumulado"]
    assert ev.result_rows == [["2024-01", 9.25, 9.25], ["2024-02", 10.0, 19.25], ["2024-03", 15.0, 34.25]]
    assert "OVER (ORDER BY mes" in ev.sql


def test_top_month_and_quarter(lab):
    session = lab("ventas.csv")
    ev = run(session, QuerySpec(s.GROUP_TOP, s.TOP, "SUM", total(), group_by="fecha", group_time="month",
                                order="DESC", answer="label"))
    assert ev.value == "2024-03"
    ev = run(session, QuerySpec(s.GROUP_AGG, s.GROUP, "COUNT", group_by="fecha", group_time="quarter"))
    assert ev.result_rows == [["2024-T1", 6]]


def test_count_groups_with_having(lab):
    session = lab("ventas.csv")
    spec = QuerySpec(s.GROUP_COUNT, s.GROUPS, "SUM", total(), group_by="categoria",
                     having=[Condition("value", ">", value=15)])
    ev = run(session, spec)
    assert ev.value == 1 and "HAVING sum_precio_total > 15" in ev.sql
    spec.having = [Condition("value", ">", value=12)]
    assert run(session, spec).value == 2


def test_product_metrics(lab):
    session = lab("ventas.csv")
    ev = run(session, QuerySpec(s.AGG_SCALAR, s.SCALAR, "SUM", Metric("product", ("cantidad", "precio_unitario"))))
    assert ev.value == pytest.approx(34.25) and "SUM((cantidad * precio_unitario))" in ev.sql
    metric = Metric("product_discount", ("cantidad", "precio_unitario", "cantidad"), discount_scale=100)
    ev = run(session, QuerySpec(s.AGG_SCALAR, s.SCALAR, "SUM", metric))
    assert "(1 - cantidad / 100)" in ev.sql and metric.label() == "cantidad x precio_unitario x (1 - cantidad/100)"
