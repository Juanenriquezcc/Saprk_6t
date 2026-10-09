"""Relations between catalog tables: proposed from metadata, measured with Spark, confirmed by the user.

Nothing here confirms a relation by itself. Names, semantic roles and statistics only PROPOSE
candidates; the user confirms, rejects or leaves each one pending, and only CONFIRMED relations
may be used later for JOINs.

Candidates (metadata only, no Spark job): between columns that can be keys (text columns and
identifier columns; never numeric measures nor dates), of two different tables, when
  - both columns have the same name ('cliente_id' / 'cliente_id'),
  - one names the other table ('cliente_id' or 'id_cliente' -> table 'clientes') and the other is
    an identifier of that table ('id', 'cliente_id'),
  - both are the column of the same dimension role (PRODUCT, CITY...).
At most config.RELATION_MAX_CANDIDATES_PER_PAIR per pair of tables. Any other pair of columns can
still be measured when the user names it.

Measures (DataFrame API, aggregates only: never the rows in Python): per side rows, null keys,
distinct keys, repeated keys, rows without a partner and coverage; keys in common; observed
cardinality among the keys in common. The cardinality is what the CURRENT data shows, not an
integrity guarantee for future data.
"""
from dataclasses import dataclass, field

import config
from app.errors import AppError
from app.schema.profiler import ID_TOKENS, NUMERIC_TYPES
from app.text import name_tokens

PENDING, CONFIRMED, REJECTED = "PENDIENTE", "CONFIRMADA", "RECHAZADA"
KEY_ROLES = sorted(role for role, kind in config.ROLE_CLASS.items() if kind == "dimension")
INTEGRAL = ("tinyint", "smallint", "int", "bigint")


@dataclass
class Side:
    table: str
    column: str
    dtype: str
    rows: int
    nulls: int
    distinct: int                 # distinct non-null keys
    duplicate_keys: int           # distinct keys present in more than one row
    matched_rows: int = None      # rows whose key exists on the other side (None: types incompatible)
    max_per_key: int = None       # most rows of one key, among the keys in common

    @property
    def non_null(self):
        return self.rows - self.nulls

    @property
    def orphans(self):
        """Rows with a key that does not exist on the other side (null keys counted apart)."""
        return None if self.matched_rows is None else self.non_null - self.matched_rows

    @property
    def coverage(self):
        if self.matched_rows is None or not self.non_null:
            return None
        return self.matched_rows / self.non_null


@dataclass
class RelationMetrics:
    left: Side
    right: Side
    compatible: bool
    matched_keys: int = None      # distinct keys present on both sides

    @property
    def cardinality(self):
        """'1:1' | '1:N' | 'N:1' | 'N:M' observed among the keys in common; None without any."""
        if not self.matched_keys:
            return None
        many = (self.left.max_per_key > 1, self.right.max_per_key > 1)
        return {(False, False): "1:1", (False, True): "1:N", (True, False): "N:1", (True, True): "N:M"}[many]

    @property
    def many_to_many(self):
        return self.cardinality == "N:M"

    def blockers(self):
        """Reasons why the relation cannot be confirmed at all."""
        if not self.compatible:
            return [f"Tipos incompatibles: {self.left.table}.{self.left.column} es {self.left.dtype} y "
                    f"{self.right.table}.{self.right.column} es {self.right.dtype}. Convierta el tipo en el ETL."]
        if not self.matched_keys:
            return ["Ninguna clave coincide entre las dos columnas: no es una relacion."]
        return []

    def warnings(self):
        """What the user must accept explicitly before confirming."""
        notes = []
        if self.compatible and _integral(self.left.dtype) != _integral(self.right.dtype):
            notes.append(f"Se comparan enteros con decimales ({self.left.dtype} y {self.right.dtype}).")
        for side, other in ((self.left, self.right), (self.right, self.left)):
            if side.nulls:
                notes.append(f"{side.table}.{side.column}: {side.nulls} registro(s) con clave nula (no se unen con nada).")
            if side.orphans:
                notes.append(f"{side.orphans} registro(s) de {side.table} sin correspondencia en {other.table}.")
            if side.duplicate_keys:
                notes.append(f"{side.table}.{side.column} tiene {side.duplicate_keys} valor(es) de clave repetidos.")
        if self.many_to_many:
            notes.append("Relacion N:M: un JOIN repite filas de ambos lados. NO es segura para sumar ni "
                         "promediar medidas.")
        return notes


@dataclass
class Relation:
    left: str                     # alias of a catalog table
    left_column: str
    right: str
    right_column: str
    reasons: list = field(default_factory=list)
    status: str = PENDING
    metrics: RelationMetrics = None

    @property
    def key(self):
        return frozenset({(self.left, self.left_column), (self.right, self.right_column)})

    def label(self):
        return f"{self.left}.{self.left_column} = {self.right}.{self.right_column}"

    def involves(self, alias):
        return alias in (self.left, self.right)


class RelationSet:
    """Relations among the tables of one catalog (candidates, confirmed and rejected)."""

    def __init__(self):
        self._items = {}

    def __iter__(self):
        return iter(list(self._items.values()))

    def __len__(self):
        return len(self._items)

    def with_status(self, status):
        return [r for r in self if r.status == status]

    def confirmed(self):
        return self.with_status(CONFIRMED)

    def pending(self):
        return self.with_status(PENDING)

    def find(self, left, left_column, right, right_column):
        return self._items.get(frozenset({(left, left_column), (right, right_column)}))

    def add(self, catalog, left, left_column, right, right_column, reason="indicada por el usuario"):
        """A candidate (new, or the existing one with one more reason). Never changes a status."""
        a, b = catalog.get(left), catalog.get(right)
        if a.alias == b.alias:
            raise AppError("Una relacion une dos datasets distintos.")
        for entry, column in ((a, left_column), (b, right_column)):
            if column not in entry.df.columns:
                raise AppError(f"La columna '{column}' no existe en '{entry.alias}'.",
                               "Columnas: " + ", ".join(entry.df.columns))
        relation = self._items.setdefault(frozenset({(a.alias, left_column), (b.alias, right_column)}),
                                          Relation(a.alias, left_column, b.alias, right_column))
        if reason not in relation.reasons:
            relation.reasons.append(reason)
        return relation

    def propose(self, catalog):
        """Adds the candidates found in the metadata of the catalog. Returns them in order."""
        proposed = []
        for columns, reasons in candidates(list(catalog)):
            for reason in reasons:
                relation = self.add(catalog, *columns, reason=reason)
            proposed.append(relation)
        return proposed

    def measure(self, catalog, relation):
        relation.metrics = measure(catalog.get(relation.left), relation.left_column,
                                   catalog.get(relation.right), relation.right_column)
        return relation.metrics

    def confirm(self, relation, accept_warnings=False):
        if relation.metrics is None:
            raise AppError("Mida la relacion antes de confirmarla.")
        blockers = relation.metrics.blockers()
        if blockers:
            raise AppError(f"No se puede confirmar {relation.label()}: {blockers[0]}")
        warnings = relation.metrics.warnings()
        if warnings and not accept_warnings:
            raise AppError(f"{relation.label()} tiene advertencias: confirmela explicitamente.", " ".join(warnings))
        relation.status = CONFIRMED

    def reject(self, relation):
        relation.status = REJECTED

    def leave_pending(self, relation):
        relation.status = PENDING

    def drop_table(self, alias):
        """A table left the catalog: its relations (any status) are no longer valid."""
        for key in [k for k, r in self._items.items() if r.involves(alias)]:
            del self._items[key]


# --- candidates ------------------------------------------------------------------------

def candidates(entries):
    """[((left, left_column, right, right_column), [reasons])] from metadata only."""
    found = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            pair = []
            for ca in _key_columns(a):
                for cb in _key_columns(b):
                    reasons = _reasons(a, ca.name, b, cb.name)
                    if reasons:
                        pair.append(((a.alias, ca.name, b.alias, cb.name), reasons))
            pair.sort(key=lambda item: -len(item[1]))           # stable: then column order
            found += pair[: config.RELATION_MAX_CANDIDATES_PER_PAIR]
    return found


def _key_columns(entry):
    return [c for c in entry.profile.columns
            if (c.kind == "text" and not c.is_date) or (c.kind == "numeric" and c.is_id)]


def _reasons(a, col_a, b, col_b):
    ta, tb = name_tokens(col_a), name_tokens(col_b)
    reasons = []
    if ta == tb:
        reasons.append("mismo nombre de columna")
    for col, tokens, other, other_tokens, table in ((col_a, ta, col_b, tb, b.alias), (col_b, tb, col_a, ta, a.alias)):
        if _references(tokens, table) and (set(other_tokens) <= ID_TOKENS or _references(other_tokens, table)):
            reasons.append(f"'{col}' parece referirse a la tabla '{table}' ('{other}' la identifica)")
    for role in KEY_ROLES:
        if a.semantic.resolved(role) == col_a and b.semantic.resolved(role) == col_b:
            reasons.append(f"mismo rol semantico ({role})")
    return reasons


def _references(tokens, table):
    """'cliente_id' / 'id_cliente' refer to the table 'clientes' (simple plural stripping)."""
    if len(tokens) < 2:
        return False
    if tokens[-1] in ID_TOKENS:
        rest = tokens[:-1]
    elif tokens[0] in ID_TOKENS:
        rest = tokens[1:]
    else:
        return False
    return "_".join(rest) in table_words(table)


def table_words(table):
    """'clientes' -> {'clientes', 'cliente', 'client'}: how a question or a column names the table."""
    # ponytail: plural stripping only (-s / -es); irregular names need the manual pair option.
    return {table, table[:-1] if table.endswith("s") else table, table[:-2] if table.endswith("es") else table}


# --- measures --------------------------------------------------------------------------

def type_family(dtype):
    base = dtype.split("(")[0]
    if base in NUMERIC_TYPES:
        return "numero"
    if base in ("date", "timestamp", "timestamp_ntz"):
        return "fecha"
    return {"string": "texto", "boolean": "booleano"}.get(base, dtype)


def _integral(dtype):
    return dtype in INTEGRAL or (dtype.startswith("decimal") and dtype.rstrip(")").endswith(",0"))


def _col(name):
    from pyspark.sql import functions as F
    return F.col("`" + name.replace("`", "``") + "`")


def _side(entry, column):
    """Per-side aggregates and the per-key counts (key, n) as a DataFrame (not collected)."""
    from pyspark.sql import functions as F
    df = entry.df
    if column not in df.columns:
        raise AppError(f"La columna '{column}' no existe en '{entry.alias}'.")
    totals = df.agg(F.count(F.lit(1)).alias("rows"), F.count(_col(column)).alias("non_null")).first()
    per_key = df.where(_col(column).isNotNull()).groupBy(_col(column).alias("k")).count()
    keys = per_key.agg(F.count(F.lit(1)).alias("distinct"),
                       F.sum(F.when(F.col("count") > 1, 1).otherwise(0)).alias("dups")).first()
    side = Side(entry.alias, column, dict(df.dtypes)[column], totals["rows"], totals["rows"] - totals["non_null"],
                keys["distinct"], keys["dups"] or 0)
    return side, per_key


def measure(left_entry, left_column, right_entry, right_column):
    """RelationMetrics of left.left_column = right.right_column, computed by Spark (no view is changed)."""
    from pyspark.sql import functions as F
    left, left_keys = _side(left_entry, left_column)
    right, right_keys = _side(right_entry, right_column)
    compatible = type_family(left.dtype) == type_family(right.dtype)
    metrics = RelationMetrics(left, right, compatible)
    if not compatible:          # Spark (ANSI) would fail comparing them: nothing to match
        return metrics
    joined = left_keys.withColumnRenamed("count", "ln").join(right_keys.withColumnRenamed("count", "rn"), "k")
    m = joined.agg(F.count(F.lit(1)).alias("keys"), F.sum("ln").alias("lrows"), F.sum("rn").alias("rrows"),
                   F.max("ln").alias("lmax"), F.max("rn").alias("rmax")).first()
    metrics.matched_keys = m["keys"]
    left.matched_rows, right.matched_rows = m["lrows"] or 0, m["rrows"] or 0
    left.max_per_key, right.max_per_key = m["lmax"], m["rmax"]
    return metrics


def explain(relation):
    """Lines for the screen: what was measured, in plain words."""
    lines = [f"Relacion: {relation.label()}   [{relation.status}]",
             "Motivo: " + "; ".join(relation.reasons)]
    m = relation.metrics
    if m is None:
        return lines + ["(sin medir)"]
    l, r = m.left, m.right
    lines.append(f"Tipos: {l.dtype} / {r.dtype} ({'compatibles' if m.compatible else 'INCOMPATIBLES'})")

    def pct(v):
        return "-" if v is None else f"{v * 100:.1f}%"

    def val(v):
        return "-" if v is None else str(v)

    width = max(len(l.table), len(r.table), 12) + 2
    lines.append(f"{'':<22}{l.table:>{width}}{r.table:>{width}}")
    for name, a, b in (("Registros", l.rows, r.rows), ("Claves nulas", l.nulls, r.nulls),
                       ("Valores distintos", l.distinct, r.distinct), ("Claves repetidas", l.duplicate_keys, r.duplicate_keys),
                       ("Sin correspondencia", val(l.orphans), val(r.orphans)), ("Cobertura", pct(l.coverage), pct(r.coverage))):
        lines.append(f"{name:<22}{a:>{width}}{b:>{width}}")
    if m.compatible:
        card = m.cardinality
        meaning = {"1:1": f"uno en {l.table}, uno en {r.table}", "1:N": f"uno en {l.table}, varios en {r.table}",
                   "N:1": f"varios en {l.table}, uno en {r.table}", "N:M": "varios en ambos lados"}.get(card, "")
        lines.append(f"Claves en comun: {m.matched_keys}   Cardinalidad observada: {card or 'ninguna'}"
                     + (f" ({meaning})" if meaning else ""))
        lines.append("Nota: la cardinalidad es la que muestran los datos actuales, no una garantia para datos futuros.")
    for text in m.blockers():
        lines.append("BLOQUEO: " + text)
    for text in m.warnings():
        lines.append("Advertencia: " + text)
    return lines
