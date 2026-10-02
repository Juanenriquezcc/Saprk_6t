"""Dataset profile computed with Spark aggregations.

Cost: one aggregation job for every column at once, one small job for the values of
categorical columns, and one `limit(n)` for sample rows. Nothing row-by-row in Python.
"""
import re
import time
from dataclasses import dataclass, field

import config
from app.text import name_tokens

NUMERIC_TYPES = ("tinyint", "smallint", "int", "bigint", "float", "double", "decimal")
DATE_TYPES = ("date", "timestamp")
ISO_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
SLASH_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")
ID_TOKENS = {"id", "codigo", "code", "key", "uuid", "identificador", "nro"}


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    kind: str                     # numeric | text | date | boolean | other
    nulls: int = 0
    null_pct: float = 0.0
    approx_distinct: int = None
    min: object = None
    max: object = None
    mean: float = None
    stddev: float = None
    date_format: str = None       # Spark pattern when a text column holds dates
    is_id: bool = False
    is_categorical: bool = False
    values: list = field(default_factory=list)   # distinct values of categorical columns

    @property
    def is_date(self):
        return self.kind == "date" or self.date_format is not None


@dataclass
class DatasetProfile:
    rows: int
    columns: list
    samples: list
    seconds: float = 0.0

    def column(self, name):
        return next((c for c in self.columns if c.name == name), None)

    def of_kind(self, kind):
        return [c for c in self.columns if c.kind == kind]

    @property
    def date_columns(self):
        return [c for c in self.columns if c.is_date]

    @property
    def categorical(self):
        return [c for c in self.columns if c.is_categorical]


def column_kind(dtype):
    if dtype.startswith(NUMERIC_TYPES):
        return "numeric"
    if dtype.startswith(DATE_TYPES):
        return "date"
    if dtype == "string":
        return "text"
    if dtype == "boolean":
        return "boolean"
    return "other"


def profile_dataset(df, rows):
    from pyspark.sql import functions as F

    start = time.perf_counter()
    columns = [ColumnProfile(name=n, dtype=t, kind=column_kind(t)) for n, t in df.dtypes]

    # Single pass: non-null count, approximate cardinality and basic statistics.
    exprs = []
    for i, c in enumerate(columns):
        col = F.col(_q(c.name))
        exprs.append(F.count(col).alias(f"nn_{i}"))
        if c.kind != "other":
            exprs.append(F.approx_count_distinct(col).alias(f"d_{i}"))
        if c.kind in ("numeric", "date"):
            exprs += [F.min(col).alias(f"min_{i}"), F.max(col).alias(f"max_{i}")]
        if c.kind == "numeric":
            exprs += [F.avg(col).alias(f"avg_{i}"), F.stddev(col).alias(f"std_{i}")]
    stats = df.agg(*exprs).first().asDict()

    samples = [r.asDict() for r in df.limit(config.PROFILE_SAMPLE_ROWS).collect()]

    for i, c in enumerate(columns):
        non_null = stats[f"nn_{i}"]
        c.nulls = rows - non_null
        c.null_pct = 100.0 * c.nulls / rows if rows else 0.0
        c.approx_distinct = stats.get(f"d_{i}")
        c.min, c.max = stats.get(f"min_{i}"), stats.get(f"max_{i}")
        c.mean, c.stddev = stats.get(f"avg_{i}"), stats.get(f"std_{i}")
        if c.kind == "text":
            c.date_format = _text_date_format([s[c.name] for s in samples])
        c.is_id = _looks_like_id(c, non_null)
        c.is_categorical = _looks_categorical(c, non_null)

    _collect_category_values(df, [c for c in columns if c.is_categorical][: config.CATEGORY_MAX_COLUMNS])
    return DatasetProfile(rows=rows, columns=columns, samples=samples, seconds=time.perf_counter() - start)


def _looks_like_id(c, non_null):
    if any(t in ID_TOKENS for t in name_tokens(c.name)):
        return True
    # A text column with a different value on every row is an identifier, not a category.
    return (c.kind == "text" and c.date_format is None and non_null > 1
            and (c.approx_distinct or 0) >= config.ID_MIN_UNIQUE_RATIO * non_null)


def _looks_categorical(c, non_null):
    if c.kind == "boolean":
        return True
    if c.kind != "text" or c.is_id or c.date_format or not c.approx_distinct:
        return False
    # The ratio rule only makes sense with enough rows (a 6-row file has few repeats).
    ratio_ok = non_null < 100 or c.approx_distinct <= config.CATEGORY_MAX_RATIO * non_null
    return c.approx_distinct <= config.CATEGORY_MAX_DISTINCT and ratio_ok


def _text_date_format(values):
    """Spark date pattern if the sample values of a text column look like dates."""
    values = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not values:
        return None
    if all(ISO_DATE.match(v) for v in values):
        return "yyyy-MM-dd"
    matches = [SLASH_DATE.match(v) for v in values]
    if all(matches):
        sep = "/" if "/" in values[0] else "-"
        # Day first unless a second part above 12 proves month first.
        month_first = any(int(m.group(2)) > 12 for m in matches)
        return f"MM{sep}dd{sep}yyyy" if month_first else f"dd{sep}MM{sep}yyyy"
    return None


def _collect_category_values(df, columns):
    """Distinct values of low-cardinality columns, so questions can mention them ('AAPL')."""
    if not columns:
        return
    from pyspark.sql import functions as F

    exprs = [F.slice(F.array_sort(F.collect_set(F.col(_q(c.name)))), 1, config.CATEGORY_VALUES_LIMIT).alias(f"v_{i}")
             for i, c in enumerate(columns)]
    row = df.agg(*exprs).first()
    for i, c in enumerate(columns):
        c.values = list(row[f"v_{i}"] or [])


def _q(name):
    return "`" + name.replace("`", "``") + "`"
