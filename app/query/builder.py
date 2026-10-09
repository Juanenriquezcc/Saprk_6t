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
    if "." in name:     # 'alias.column' of a JOIN (catalog aliases and column names are validated)
        table, column = name.split(".", 1)
        return f"{ident(table)}.{ident(column)}"
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
    if metric.kind == "product":
        return f"({cols[0]} * {cols[1]})"
    if metric.kind == "product_discount":
        discount = cols[2] if metric.discount_scale == 1 else f"{cols[2]} / {metric.discount_scale}"
        return f"({cols[0]} * {cols[1]} * (1 - {discount}))"
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


def _bare(name):
    """'pedidos.unidades' -> 'unidades': result column names never carry the table."""
    return name.rsplit(".", 1)[-1]


JOIN_SQL = {"INNER": "JOIN", "LEFT": "LEFT JOIN", "ANTI": "LEFT ANTI JOIN"}


def from_sql(spec):
    """The `dataset` view, or a catalog table with the JOINs of its confirmed relations."""
    if not spec.base_table:
        return config.VIEW_NAME
    parts = [ident(spec.base_table)]
    for j in spec.joins:
        if j.kind not in JOIN_SQL:
            raise ValueError(f"Tipo de JOIN no admitido: {j.kind}")
        on = [f"{ident(j.left)} = {ident(j.right)}"] + [sql for sql, table in _filter_parts(spec) if table == j.table]
        parts.append(f"{JOIN_SQL[j.kind]} {ident(j.table)} ON " + " AND ".join(on))
    return "\n".join(parts)


def _filter_parts(spec):
    """[(condition SQL, outer table or None)]. A condition on the table of a LEFT / ANTI JOIN goes to
    its ON: in WHERE it would discard the rows the LEFT JOIN keeps (or change which rows ANTI finds)."""
    outer = {j.table for j in spec.joins if j.kind in ("LEFT", "ANTI")}

    def table_of(*columns):
        return next((c.split(".", 1)[0] for c in columns if c and "." in c and c.split(".", 1)[0] in outer), None)

    return ([(condition_sql(c), table_of(c.column, c.other_column)) for c in spec.filters]
            + [(date_filter_sql(d), table_of(d.column)) for d in spec.date_filters])


def output_names(columns):
    """'alias.column' -> result name: the column, or 'alias__column' when two tables share it."""
    bare = [_bare(c) for c in columns]
    return {c: (b if bare.count(b) == 1 else c.replace(".", "__")) for c, b in zip(columns, bare)}


def value_alias(spec):
    agg = spec.aggregation
    if agg == "COUNT":
        return "total_registros"
    if agg == "COUNT_DISTINCT":
        return f"distintos_{_slug(_bare(spec.target.columns[0]))}"
    if agg == "PERCENT":
        return "porcentaje"
    if agg == "PERIOD_CHANGE":
        return "variacion_pct_periodo"
    if spec.target.kind == "difference":
        name = f"{_bare(spec.target.columns[0])}_menos_{_bare(spec.target.columns[1])}"
    elif spec.target.kind == "pct_change":
        name = "variacion_pct"
    elif spec.target.kind in ("product", "product_discount"):
        name = "ingresos" if spec.target.kind == "product" else "ingresos_con_descuento"
    else:
        name = _bare(spec.target.columns[0])
    prefix = AGG_PREFIX.get(agg, f"{agg.lower()}_") if agg else ""   # RECORD has no aggregation
    if agg == "PERCENTILE":
        prefix = f"percentil_{format(spec.percentile * 100, 'g').replace('.', '_')}_"
    return f"{prefix}{_slug(name)}"


AGG_PREFIX = {"MEDIAN": "mediana_", "STDDEV": "desviacion_", "VARIANCE": "varianza_"}
AGG_SQL = {"MEDIAN": "percentile({m}, 0.5)", "STDDEV": "STDDEV({m})", "VARIANCE": "VARIANCE({m})"}
TIME_SQL = {   # group expression and column name of each time grain
    "year": ("year({d})", "anio"),
    "quarter": ("concat(year({d}), '-T', quarter({d}))", "trimestre"),
    "month": ("date_format({d}, 'yyyy-MM')", "mes"),
    "day": ("{d}", "dia"),
}


def group_sql(spec):
    """(SELECT expression, result column name) of the grouping."""
    if not spec.group_time:
        if spec.joins:      # 'clientes.ciudad AS ciudad': ORDER BY never sees two columns with that name
            return f"{ident(spec.group_by)} AS {ident(_bare(spec.group_by))}", _bare(spec.group_by)
        return ident(spec.group_by), spec.group_by
    expr, name = TIME_SQL[spec.group_time]
    d = date_expr(spec.group_by, spec.group_date_kind, spec.group_date_format)
    return f"{expr.format(d=d)} AS {name}", name


def aggregate_sql(spec):
    agg = spec.aggregation
    if agg == "COUNT":
        return f"COUNT({ident(spec.count_column)})" if spec.count_column else "COUNT(*)"
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
    if agg == "PERCENTILE":
        return f"percentile({metric_sql(spec.target)}, {spec.percentile})"
    if agg in AGG_SQL:
        return AGG_SQL[agg].format(m=metric_sql(spec.target))
    return f"{agg}({metric_sql(spec.target)})"


def where_sql(spec, extra=()):
    parts = [sql for sql, outer in _filter_parts(spec) if outer is None] + list(extra)
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
    view = from_sql(spec)
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
        names = output_names(spec.select)
        alias = value_alias(spec) if derived else names.get(spec.target.columns[0], spec.target.columns[0])
        # Over JOINs every column is listed with a unique name (two tables may share one).
        columns = ", ".join(f"{ident(c)} AS {ident(n)}" for c, n in names.items()) if spec.select else "*"
        select = f"SELECT {columns}, {m} AS {alias}" if derived else f"SELECT {columns}"
        sql = (f"{select}\nFROM {view}{where_sql(spec, [f'{m} IS NOT NULL'])}\n"
               f"ORDER BY {m} {spec.order or 'DESC'}\nLIMIT {spec.limit or 1}")
        answer = spec.answer.split(":", 1)[1] if spec.answer.startswith("column:") else None
        return BuiltQuery(sql, value_column=alias, label_column=names.get(answer) if answer else None)

    alias = value_alias(spec)
    value = aggregate_sql(spec)
    if spec.shape == s.SCALAR:
        return BuiltQuery(f"SELECT {value} AS {alias}\nFROM {view}{where_sql(spec)}", value_column=alias)

    g_select, g_name = group_sql(spec)
    g = g_select.rsplit(" AS ", 1)[0] if (spec.group_time or spec.joins) else g_select
    g_order = ident(g_name)
    having = ""
    if spec.having:
        having = "\nHAVING " + " AND ".join(f"{alias} {h.op} {literal(h.value)}" for h in spec.having)
    grouped = f"SELECT {g_select},\n       {value} AS {alias}\nFROM {view}{where_sql(spec)}\nGROUP BY {g}{having}"

    if spec.shape == s.GROUPS:
        return BuiltQuery(f"SELECT COUNT(*) AS total_grupos\nFROM (\n{grouped}\n) t", value_column="total_grupos")

    limit = spec.limit or (1 if spec.shape == s.TOP else config.MAX_GROUP_ROWS)
    if spec.cumulative:
        # Running total over the periods (window function over the small grouped result).
        sql = (f"SELECT {g_order}, {alias},\n       SUM({alias}) OVER (ORDER BY {g_order} "
               f"ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS acumulado\nFROM (\n{grouped}\n) t\n"
               f"ORDER BY {g_order} ASC\nLIMIT {limit}")
        return BuiltQuery(sql, value_column=alias, label_column=g_name)
    if spec.group_time and spec.shape == s.GROUP and not spec.order:
        order_by = f"{g_order} ASC"          # a time series reads in date order
    else:
        order_by = f"{alias} {spec.order or 'DESC'} NULLS LAST, {g_order} ASC"
    sql = f"{grouped}\nORDER BY {order_by}\nLIMIT {limit}"
    return BuiltQuery(sql, value_column=alias, label_column=g_name)


def build_tie_check(spec, built, top_value):
    """Counts how many groups/rows share the best value (to report ties honestly)."""
    view = from_sql(spec)
    if spec.shape == s.RECORD:
        m = metric_sql(spec.target)
        return f"SELECT COUNT(*) AS empates\nFROM {view}{where_sql(spec, [f'{m} = {literal(top_value)}'])}"
    inner = built.sql.split("\nORDER BY")[0]
    return f"SELECT COUNT(*) AS empates\nFROM (\n{inner}\n) t\nWHERE {built.value_column} = {literal(top_value)}"


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "valor"
