"""Guided analysis: each operation is a small factory that returns a QuerySpec.

No SQL here: the specs go through the same builder, executor and evidence as questions.
"""
import datetime
import re

from app.errors import AppError
from app.query import spec as s
from app.query.spec import Condition, DateFilter, Metric, QuerySpec
from app.questions.numbers import read_number

OPERATORS = (">", ">=", "<", "<=", "=", "!=")
NUMERIC_STATS = ["no_nulos", "nulos", "distintos", "minimo", "maximo", "promedio", "desviacion", "suma"]
OTHER_STATS = ["no_nulos", "nulos", "distintos", "minimo", "maximo"]


def column_stats(profile, column):
    col = profile.column(column)
    stats = NUMERIC_STATS if col.kind == "numeric" else OTHER_STATS
    return QuerySpec(s.COLUMN_SUMMARY, s.SCALAR, "SUMMARY", columns=[column], stats=list(stats))


def group(group_by, aggregation, metric=None):
    target = Metric("column", (metric,)) if metric else None
    return QuerySpec(s.GROUP_AGG, s.GROUP, aggregation, target, group_by=group_by, order="DESC")


def ranking(group_by, aggregation, metric, order, n):
    target = Metric("column", (metric,)) if metric else None
    return QuerySpec(s.GROUP_TOP, s.TOP, aggregation, target, group_by=group_by, order=order, limit=n,
                     answer="label")


def extreme(metric, which, mode):
    """which: MAX | MIN; mode: value | record."""
    order = "DESC" if which == "MAX" else "ASC"
    if mode == "record":
        return QuerySpec(s.RECORD_EXTREME, s.RECORD, None, Metric("column", (metric,)), order=order, answer="row")
    return QuerySpec(s.AGG_SCALAR, s.SCALAR, which, Metric("column", (metric,)))


def compare(column_a, column_b, aggregation):
    return QuerySpec(s.COLUMN_COMPARISON, s.COMPARE, aggregation, comparison=(column_a, column_b),
                     order="DESC", answer="label")


def pct_change(base, final, aggregation, group_by=None, order=None, n=None):
    target = Metric("pct_change", (base, final))
    if group_by:
        shape = s.TOP if order else s.GROUP
        return QuerySpec(s.PCT_CHANGE, shape, aggregation, target, group_by=group_by, order=order or "DESC",
                         limit=n, answer="label")
    return QuerySpec(s.PCT_CHANGE, s.SCALAR, aggregation, target)


def count_where(filters, date_filters=()):
    return QuerySpec(s.COUNT_WHERE, s.SCALAR, "COUNT", filters=list(filters), date_filters=list(date_filters))


def filter_rows(filters, date_filters=(), limit=None):
    return QuerySpec(s.FILTER_ROWS, s.ROWS, None, filters=list(filters), date_filters=list(date_filters), limit=limit)


def condition(profile, column, op, raw):
    """Typed condition from user input -> (Condition | None, DateFilter | None).

    The right side may be another column of the dataset or a literal value.
    """
    if op not in OPERATORS:
        raise AppError(f"Operador no valido: {op}", f"Use uno de: {' '.join(OPERATORS)}")
    col = profile.column(column)
    text = raw.strip()
    other = profile.column(text)
    if other is not None:
        return Condition(column, op, other_column=other.name), None
    if col.is_date:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise AppError(f"Fecha no valida: '{text}'", "Use el formato aaaa-mm-dd, por ejemplo 2024-01-31.")
        try:
            datetime.date.fromisoformat(text)
        except ValueError:
            raise AppError(f"Fecha no valida: '{text}'") from None
        kind = "text" if col.kind == "text" else ("timestamp" if col.dtype.startswith("timestamp") else "date")
        return None, DateFilter(column, op, text, kind, col.date_format)
    if col.kind == "numeric":
        readings = read_number(text)
        if len(readings) != 1:
            raise AppError(f"Numero no valido o ambiguo: '{text}'", "Escriba el numero con punto decimal, sin separador de miles.")
        value = readings[0].value
        return Condition(column, op, value=int(value) if value.is_integer() else value), None
    # Text: use the stored spelling of a known category value when it matches.
    known = next((v for v in col.values if isinstance(v, str) and v.lower() == text.lower()), None)
    return Condition(column, op, value=known or text), None
