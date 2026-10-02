"""QuerySpec: the single description of an analytical query.

Question -> QuerySpec -> SQL builder -> executor -> evidence. Nothing else builds SQL.
"""
from dataclasses import asdict, dataclass, field

# Intents (what is being asked).
COUNT_ROWS = "COUNT_ROWS"
COUNT_DISTINCT = "COUNT_DISTINCT"
COUNT_WHERE = "COUNT_WHERE"
AGG_SCALAR = "AGG_SCALAR"
AGG_WHERE = "AGG_WHERE"
GROUP_AGG = "GROUP_AGG"
GROUP_TOP = "GROUP_TOP"
RECORD_EXTREME = "RECORD_EXTREME"
DIFFERENCE = "DIFFERENCE"
PCT_CHANGE = "PCT_CHANGE"
PCT_CHANGE_PERIOD = "PCT_CHANGE_PERIOD"
PERCENTAGE_OF = "PERCENTAGE_OF"
COLUMN_COMPARISON = "COLUMN_COMPARISON"
COLUMN_SUMMARY = "COLUMN_SUMMARY"    # statistics of one or more columns (guided / full analysis)
FILTER_ROWS = "FILTER_ROWS"          # rows that satisfy conditions (bounded)
MANUAL_SQL = "MANUAL_SQL"            # SQL typed by the user (no QuerySpec)

# Shapes (how the SQL looks).
SCALAR = "SCALAR"      # one value
GROUP = "GROUP"        # one value per group
TOP = "TOP"            # best group(s) by value
RECORD = "RECORD"      # whole row(s) with the extreme value
COMPARE = "COMPARE"    # the same aggregation over two columns
ROWS = "ROWS"          # plain rows (bounded by LIMIT)

# Aggregations. SUMMARY computes `stats` for every column in `columns` in one query.
AGGREGATIONS = ("COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX", "PERIOD_CHANGE", "PERCENT", "SUMMARY")
SUMMARY_STATS = ("no_nulos", "nulos", "distintos", "minimo", "maximo", "promedio", "desviacion", "suma")

# Question types.
OPEN = "OPEN"
MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
TRUE_FALSE = "TRUE_FALSE"


@dataclass
class Metric:
    """Numeric expression being analysed.

    kind 'column':     columns = (col,)
    kind 'difference': columns = (a, b)          -> a - b
    kind 'pct_change': columns = (base, final)   -> (final - base) / base * 100
    """
    kind: str
    columns: tuple

    def label(self):
        if self.kind == "difference":
            return f"{self.columns[0]} - {self.columns[1]}"
        if self.kind == "pct_change":
            return f"variacion % de {self.columns[0]} a {self.columns[1]}"
        return self.columns[0]


@dataclass
class Condition:
    """column <op> value | column <op> other_column | column IN (values)."""
    column: str
    op: str                       # > >= < <= = != IN
    value: object = None
    other_column: str = None

    def label(self):
        if self.op == "IN":
            return f"{self.column} IN ({', '.join(map(str, self.value))})"
        return f"{self.column} {self.op} {self.other_column or self.value}"


@dataclass
class DateFilter:
    column: str
    op: str                       # >= <= < > =
    value: str                    # ISO date yyyy-mm-dd
    column_kind: str = "date"     # date | timestamp | text
    text_format: str = None       # Spark pattern when column_kind == 'text'

    def label(self):
        return f"{self.column} {self.op} {self.value}"


@dataclass
class QuerySpec:
    intent: str
    shape: str
    aggregation: str = None
    target: Metric = None                         # target column / expression
    group_by: str = None
    filters: list = field(default_factory=list)          # [Condition] -> WHERE
    date_filters: list = field(default_factory=list)     # [DateFilter] -> WHERE
    having: list = field(default_factory=list)           # [Condition] on the aggregated value
    percent_filters: list = field(default_factory=list)  # [Condition] counted in PERCENTAGE_OF
    order: str = None                             # ASC | DESC
    limit: int = None
    comparison: tuple = None                      # (col_a, col_b) for COLUMN_COMPARISON
    period_column: str = None                     # date column for PCT_CHANGE_PERIOD
    columns: list = field(default_factory=list)   # columns for SUMMARY
    stats: list = field(default_factory=list)     # SUMMARY_STATS to compute
    answer: str = "value"                         # value | label | row | column:<name>
    question_type: str = OPEN
    options: list = field(default_factory=list)   # [(letter, text)]
    claim: str = None                             # claimed value in TRUE_FALSE

    @property
    def percentage(self):
        return self.intent in (PERCENTAGE_OF, PCT_CHANGE, PCT_CHANGE_PERIOD)

    def columns_used(self):
        cols = []
        for c in (self.target.columns if self.target else ()):
            cols.append(c)
        cols += list(self.columns)
        for c in (self.group_by, self.period_column, *(self.comparison or ())):
            if c:
                cols.append(c)
        for f in (*self.filters, *self.percent_filters):
            cols += [f.column] + ([f.other_column] if f.other_column else [])
        cols += [d.column for d in self.date_filters]
        if self.answer.startswith("column:"):
            cols.append(self.answer.split(":", 1)[1])
        return list(dict.fromkeys(cols))

    def filter_labels(self):
        return [f.label() for f in (*self.filters, *self.date_filters)] + \
               [f"valor agregado {h.op} {h.value}" for h in self.having]

    def to_dict(self):
        return asdict(self)
