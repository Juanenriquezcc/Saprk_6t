"""SQL builder: the ONLY place where Spark SQL is generated from a QuerySpec.

Native Spark SQL functions only (no Python UDF). Divisions use NULLIF so ANSI mode
never fails on zero; text dates use try_to_date so bad values become NULL.
"""
import datetime
import re
from dataclasses import dataclass
from decimal import Decimal

import config
from app.query import spec as s

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = {
    "all", "and", "any", "as", "between", "by", "case", "cast", "date", "distinct", "else", "end",
    "false", "from", "group", "having", "in", "interval", "is", "join", "like", "limit", "not",
    "null", "on", "or", "order", "select", "table", "then", "time", "timestamp", "true", "union",
    "user", "when", "where", "with",
}


@dataclass
class BuiltQuery:
    sql: str
    value_column: str = None      # alias of the computed value
    label_column: str = None      # group / entity column in the result


def ident(name):
    if _IDENTIFIER.match(name) and name.lower() not in _RESERVED:
        return name
    return "`" + name.replace("`", "``") + "`"


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        value = float(value)
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else f"{value!r}D"
    if isinstance(value, (datetime.date, datetime.datetime)):
        return f"DATE'{value:%Y-%m-%d}'"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def metric_sql(metric):
    cols = [ident(c) for c in metric.columns]
    if metric.kind == "difference":
        return f"({cols[0]} - {cols[1]})"
    if metric.kind == "pct_change":
        base, final = cols
        return f"(({final} - {base}) / NULLIF({base}, 0) * 100)"
    return cols[0]


def condition_sql(c):
    if c.op == "IN":
        return f"{ident(c.column)} IN ({', '.join(literal(v) for v in c.value)})"
    right = ident(c.other_column) if c.other_column else literal(c.value)
    return f"{ident(c.column)} {c.op} {right}"


def date_expr(column, kind, text_format=None):
    if kind == "timestamp":
        return f"to_date({ident(column)})"
    if kind == "text":
        return f"try_to_date({ident(column)}, '{text_format or 'yyyy-MM-dd'}')"
    return ident(column)


def date_filter_sql(d):
    return f"{date_expr(d.column, d.column_kind, d.text_format)} {d.op} DATE'{d.value}'"


def value_alias(spec):
    agg = spec.aggregation
    if agg == "COUNT":
        return "total_registros"
    if agg == "COUNT_DISTINCT":
        return f"distintos_{_slug(spec.target.columns[0])}"
    if agg == "PERCENT":
        return "porcentaje"
    if agg == "PERIOD_CHANGE":
        return "variacion_pct_periodo"
    if spec.target.kind == "difference":
        name = f"{spec.target.columns[0]}_menos_{spec.target.columns[1]}"
    elif spec.target.kind == "pct_change":
        name = "variacion_pct"
    else:
        name = spec.target.columns[0]
    prefix = f"{agg.lower()}_" if agg else ""   # RECORD has no aggregation
    return f"{prefix}{_slug(name)}"


def aggregate_sql(spec):
    agg = spec.aggregation
    if agg == "COUNT":
        return "COUNT(*)"
    if agg == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {ident(spec.target.columns[0])})"
    if agg == "PERCENT":
        condition = " AND ".join(condition_sql(c) for c in spec.percent_filters) or "TRUE"
        if spec.target:
            m = metric_sql(spec.target)
            return f"100.0 * SUM(CASE WHEN {condition} THEN {m} ELSE 0 END) / NULLIF(SUM({m}), 0)"
        return f"100.0 * COUNT_IF({condition}) / NULLIF(COUNT(*), 0)"
    if agg == "PERIOD_CHANGE":
        m = metric_sql(spec.target)
        d = ident(spec.period_column)
        first, last = f"min_by({m}, {d})", f"max_by({m}, {d})"
        return f"(({last} - {first}) / NULLIF({first}, 0) * 100)"
    return f"{agg}({metric_sql(spec.target)})"


def where_sql(spec, extra=()):
    parts = [condition_sql(c) for c in spec.filters] + [date_filter_sql(d) for d in spec.date_filters] + list(extra)
    return ("\nWHERE " + "\n  AND ".join(parts)) if parts else ""


SUMMARY_SQL = {
    "no_nulos": "COUNT({c})",
    "nulos": "COUNT(*) - COUNT({c})",
    "distintos": "COUNT(DISTINCT {c})",
    "minimo": "MIN({c})",
    "maximo": "MAX({c})",
    "promedio": "AVG({c})",
    "desviacion": "STDDEV({c})",
    "suma": "SUM({c})",
}


def summary_alias(stat, column):
    return f"{stat}__{_slug(column)}"


def build_sql(spec):
    view = config.VIEW_NAME
    if spec.aggregation == "SUMMARY":
        exprs = [f"{SUMMARY_SQL[stat].format(c=ident(col))} AS {summary_alias(stat, col)}"
                 for col in spec.columns for stat in spec.stats]
        return BuiltQuery("SELECT " + ",\n       ".join(exprs) + f"\nFROM {view}{where_sql(spec)}")

    if spec.shape == s.ROWS:
        return BuiltQuery(f"SELECT *\nFROM {view}{where_sql(spec)}\nLIMIT {spec.limit or config.MAX_DISPLAY_ROWS}")

    if spec.shape == s.COMPARE:
        a, b = spec.comparison
        agg = spec.aggregation
        alias_a, alias_b = f"{agg.lower()}_{_slug(a)}", f"{agg.lower()}_{_slug(b)}"
        sql = (f"SELECT {agg}({ident(a)}) AS {alias_a},\n       {agg}({ident(b)}) AS {alias_b}\n"
               f"FROM {view}{where_sql(spec)}")
        return BuiltQuery(sql, value_column=alias_a)

    if spec.shape == s.RECORD:
        m = metric_sql(spec.target)
        derived = spec.target.kind != "column"
        alias = value_alias(spec) if derived else spec.target.columns[0]
        select = f"SELECT *, {m} AS {alias}" if derived else "SELECT *"
        sql = (f"{select}\nFROM {view}{where_sql(spec, [f'{m} IS NOT NULL'])}\n"
               f"ORDER BY {m} {spec.order or 'DESC'}\nLIMIT {spec.limit or 1}")
        return BuiltQuery(sql, value_column=alias)

    alias = value_alias(spec)
    value = aggregate_sql(spec)
    if spec.shape == s.SCALAR:
        return BuiltQuery(f"SELECT {value} AS {alias}\nFROM {view}{where_sql(spec)}", value_column=alias)

    g = ident(spec.group_by)
    having = ""
    if spec.having:
        having = "\nHAVING " + " AND ".join(f"{alias} {h.op} {literal(h.value)}" for h in spec.having)
    order = spec.order or "DESC"
    limit = spec.limit or (1 if spec.shape == s.TOP else config.MAX_GROUP_ROWS)
    sql = (f"SELECT {g},\n       {value} AS {alias}\nFROM {view}{where_sql(spec)}\nGROUP BY {g}{having}\n"
           f"ORDER BY {alias} {order} NULLS LAST, {g} ASC\nLIMIT {limit}")
    return BuiltQuery(sql, value_column=alias, label_column=spec.group_by)


def build_tie_check(spec, built, top_value):
    """Counts how many groups/rows share the best value (to report ties honestly)."""
    view = config.VIEW_NAME
    if spec.shape == s.RECORD:
        m = metric_sql(spec.target)
        return f"SELECT COUNT(*) AS empates\nFROM {view}{where_sql(spec, [f'{m} = {literal(top_value)}'])}"
    inner = built.sql.split("\nORDER BY")[0]
    return f"SELECT COUNT(*) AS empates\nFROM (\n{inner}\n) t\nWHERE {built.value_column} = {literal(top_value)}"


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "valor"
