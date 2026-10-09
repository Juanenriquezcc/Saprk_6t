"""Questions over several catalog tables: the same interpreter, then a JOIN plan from CONFIRMED relations.

1. The interpreter (intents.py) runs unchanged over a combined schema with the columns of every
   catalog table. A column name that exists in several tables is asked (which table?).
2. The plan finds the tables the question needs (its columns, and tables it names: 'pedidos'),
   connects them ONLY through confirmed relations (asks when there are several paths, refuses
   when there is none) and writes every column as 'alias.column'. The builder makes the SQL.
3. Duplication guard: a JOIN repeats the rows of a table when it crosses to a side with
   repeated keys (the '1' side of a 1:N, both sides of an N:M). SUM, AVG, COUNT... over the rows
   of a repeated table would be inflated: refused with the reason (never patched with DISTINCT).
   MAX, MIN and COUNT DISTINCT do not change with repetitions.
Special forms: 'clientes sin pedidos' / 'que no tienen pedidos' -> LEFT ANTI JOIN (direct relation);
'... incluyendo los que no tienen pedidos' -> LEFT JOIN that keeps the grouped table.
"""
import dataclasses
import re

from app.query import spec as s
from app.query.spec import Join, QuerySpec
from app.questions import lexicon as lx
from app.questions.intents import Interpreter, NeedsInput, _Need, _Run
from app.schema.profiler import DatasetProfile
from app.schema.relations import table_words
from app.schema.semantic import detect_roles
from app.schema.values import ValueLookup
from app.text import name_tokens

SAFE_WITH_REPEATS = {"MAX", "MIN", "COUNT_DISTINCT", None}     # None: a record or plain rows
MAX_PATH = 3                                                    # relations between two tables
HINT = "Confirme la relacion en Herramientas avanzadas > 13 (Relaciones entre datasets)."


class _Stop(Exception):
    def __init__(self, need):
        super().__init__(need.message)
        self.need = need


def _refuse(message, missing=()):
    raise _Stop(NeedsInput("join", message, [], missing=list(missing)))


def combine(catalog):
    """(DatasetProfile with the columns of every table, {column: [aliases that have it]})."""
    columns, by_name, owners = [], {}, {}
    for entry in catalog:
        for c in entry.profile.columns:
            owners.setdefault(c.name, []).append(entry.alias)
            merged = by_name.get(c.name)
            if merged is None:
                by_name[c.name] = merged = dataclasses.replace(c, values=list(c.values))
                columns.append(merged)
            else:           # the same name in another table: one entry in the schema, values of both
                merged.values = list(dict.fromkeys(merged.values + list(c.values)))
                merged.is_categorical = merged.is_categorical or c.is_categorical
                merged.values_complete = merged.values_complete and c.values_complete
    return DatasetProfile(rows=sum(e.profile.rows for e in catalog), columns=columns, samples=[]), owners


class CatalogInterpreter:
    """Interpreter of questions over the catalog tables (same contract as intents.Interpreter)."""

    def __init__(self, catalog, choices=None):
        self.catalog = catalog
        self.choices = choices if choices is not None else {}
        self._built = None

    def _schema(self):
        signature = tuple((e.alias, id(e)) for e in self.catalog)
        if self._built is None or self._built[0] != signature:
            profile, owners = combine(self.catalog)
            entries = list(self.catalog)
            # A new catalog state gets a new lookup: its cache never answers for tables that changed.
            lookup = ValueLookup(lambda: [(e.df, e.profile) for e in entries])
            words = {w for e in entries for w in table_words(e.alias)}
            columns = {w.replace("_", " "): list(e.df.columns) for e in entries for w in table_words(e.alias)}
            self._built = (signature, profile, owners,
                           Interpreter(profile, detect_roles(profile), self.choices, lookup, words, columns, owners))
        return self._built

    @property
    def profile(self):
        return self._schema()[1]

    def interpret(self, parsed, extra_choices=None):
        if not len(self.catalog):
            return NeedsInput("catalog", "No hay datasets registrados en el catalogo.", [],
                              missing=["datasets registrados (Herramientas avanzadas > 11)"])
        _, profile, owners, interpreter = self._schema()
        plan = _Plan(self, parsed, extra_choices or {}, profile, owners, interpreter)
        try:
            keep = plan.keep_unmatched()
            if keep is None:
                anti = plan.anti()
                if anti is not None:
                    return anti
            text = parsed.normalized[:keep.start()] if keep else parsed.normalized
            spec = interpreter.interpret(dataclasses.replace(parsed, normalized=text.strip()), extra_choices)
            if isinstance(spec, NeedsInput):
                if spec.key == "unrecognized":     # no table nor column identified: say what exists
                    spec.missing.append("una columna de alguno de los datasets registrados: " + "; ".join(
                        f"{e.alias} ({', '.join(e.df.columns)})" for e in self.catalog))
                return spec
            return plan.apply(spec, keep is not None)
        except (_Stop, _Need) as stop:
            return stop.need


class _Plan:
    def __init__(self, ci, parsed, extra, profile, owners, interpreter):
        self.catalog, self.choices, self.extra, self.interpreter = ci.catalog, ci.choices, extra, interpreter
        self.parsed, self.text, self.owners = parsed, parsed.normalized, owners
        self.column_words = {" ".join(name_tokens(c.name)) for c in profile.columns}

    # --- choices ---------------------------------------------------------------------

    def _ask(self, key, message, options):
        chosen = self.extra.get(key, self.choices.get(key))
        if chosen in [value for _, value in options]:       # a remembered choice still valid
            return chosen
        raise _Stop(NeedsInput(key, message, options, remember=True))

    def _table_of(self, column):
        owners = self.owners.get(column) or _refuse(f"La columna '{column}' no esta en ningun dataset del catalogo.")
        if len(owners) == 1:
            return owners[0]
        return self._ask(f"table:{column}", f"La columna '{column}' existe en varias tablas ({', '.join(owners)}). "
                                            "Seleccione de cual tabla se toma.", [(o, o) for o in owners])

    # --- table words --------------------------------------------------------------------

    def _words(self, alias, named_only=False):
        words = sorted(table_words(alias), key=len, reverse=True)
        if named_only and any(w.replace("_", " ") in self.column_words for w in words):
            return None             # 'producto' is also a column: the word names the column, not the table
        return r"(?<![\w@])(?:" + "|".join(re.escape(w).replace("_", r"\s+") for w in words) + r")(?![\w@])"

    def mentioned(self):
        return [e.alias for e in self.catalog
                if self._words(e.alias, True) and re.search(self._words(e.alias, True), self.text)]

    def counted(self):
        found = [(m.start(), e.alias) for e in self.catalog
                 for m in [re.search(lx.COUNT_HEAD + self._words(e.alias), self.text)] if m]
        return min(found)[1] if found else None

    def keep_unmatched(self):
        m = re.search(lx.KEEP_UNMATCHED, self.text)
        return m if m and re.search(lx.NEGATION, m.group(0)) else None

    # --- relations ------------------------------------------------------------------------

    def _edges(self):
        return [r for r in self.catalog.relations.confirmed() if r.metrics is not None and r.metrics.compatible]

    def _paths(self, a, b):
        edges, found = self._edges(), []

        def walk(node, visited, path):
            if node == b:
                found.append(path)
                return
            if len(path) == MAX_PATH:
                return
            for r in edges:
                if r.involves(node):
                    other = r.right if r.left == node else r.left
                    if other not in visited:
                        walk(other, visited | {other}, path + [r])
        walk(a, {a}, [])
        return found

    def path(self, a, b):
        found = self._paths(a, b)
        if not found:
            self._no_relation(a, b)
        if len(found) == 1:
            return found[0]
        options = [(" -> ".join(r.label() for r in p), " | ".join(r.label() for r in p)) for p in found]
        chosen = self._ask(f"path:{min(a, b)}|{max(a, b)}", f"Hay {len(found)} caminos de relaciones confirmadas "
                                                            f"entre {a} y {b}. Seleccione cual usar.", options)
        return found[[value for _, value in options].index(chosen)]

    def _no_relation(self, a, b):
        notes = []
        for r in self.catalog.relations:
            if {r.left, r.right} == {a, b}:
                blockers = r.metrics.blockers() if r.metrics else []
                notes.append(f"La relacion {r.label()} esta {r.status}" + (f": {blockers[0]}" if blockers else "."))
        _refuse(" ".join([f"No hay una relacion confirmada entre {a} y {b}: no se pueden combinar esas tablas."]
                         + notes), [HINT])

    # --- plan -----------------------------------------------------------------------------

    def apply(self, spec, keep):
        table_of = {c: self._table_of(c) for c in spec.columns_used()}
        tables = list(dict.fromkeys(list(table_of.values()) + self.mentioned()))
        if not tables:
            tables = [self._ask("table:rows", "Seleccione el dataset sobre el que se pregunta.",
                                [(e.alias, e.alias) for e in self.catalog])]
        if len(tables) == 1 and not keep:
            spec.base_table = tables[0]            # one table: same query as always, on its view
            return spec
        edges = self._tree(tables)
        subject = self._check_repeats(spec, table_of, tables, edges)
        base = self._keep_base(spec, table_of, tables) if keep else (subject or tables[0])
        spec.base_table, spec.joins = base, self._joins(base, edges, "LEFT" if keep else "INNER")
        if keep and spec.aggregation == "COUNT" and spec.target is None:
            spec.count_column = spec.joins[0].right            # COUNT of the joined key: 0 when no partner
        qualified = {c: f"{t}.{c}" for c, t in table_of.items()}
        _qualify(spec, qualified)
        if not keep:
            spec.notes += self._left_out(base, edges)
        if spec.shape == s.RECORD:
            spec.select = [f"{t}.{c}" for t in spec.tables for c in self.catalog.get(t).df.columns]
        return spec

    def _tree(self, tables):
        root, nodes, edges = tables[0], {tables[0]}, []
        for t in tables[1:]:
            for r in self.path(root, t):
                if r not in edges:
                    edges.append(r)
                    nodes.update((r.left, r.right))
        if len(edges) != len(nodes) - 1:
            _refuse("Los caminos de relaciones elegidos forman un ciclo entre las tablas; no se puede "
                    "decidir como combinarlas.", ["una pregunta sobre menos tablas"])
        return edges

    def _joins(self, base, edges, kind):
        joins, present, pending = [], {base}, list(edges)
        while pending:
            r = next(r for r in pending if r.left in present or r.right in present)
            pending.remove(r)
            near, far = (r.left, r.right) if r.left in present else (r.right, r.left)
            near_col, far_col = (r.left_column, r.right_column) if r.left == near else (r.right_column, r.left_column)
            for t, c in ((near, near_col), (far, far_col)):
                if c not in self.catalog.get(t).df.columns:
                    _refuse(f"La relacion {r.label()} usa la columna '{c}', que ya no existe en {t}.", [HINT])
            joins.append(Join(kind, far, f"{near}.{near_col}", f"{far}.{far_col}", r.label()))
            present.add(far)
        return joins

    @staticmethod
    def _left_out(base, edges):
        """INNER JOIN: rows of the base table without a partner (orphan or null key) are not in the result."""
        notes = []
        for r in edges:
            for side, other in ((r.metrics.left, r.metrics.right), (r.metrics.right, r.metrics.left)):
                lost = (side.orphans or 0) + side.nulls
                if side.table == base and lost:
                    notes.append(f"INNER JOIN: {lost} registro(s) de {base} sin correspondencia en {other.table} "
                                 f"(clave nula o inexistente, segun la medicion de la relacion {r.label()}) "
                                 "no entran en el resultado.")
        return notes

    @staticmethod
    def _repeats(table, edges):
        """Relations that repeat the rows of `table` in the JOIN (crossing to a side with repeated keys)."""
        found, seen, frontier = [], {table}, [table]
        while frontier:
            node = frontier.pop()
            for r in edges:
                if r.involves(node):
                    other = r.right if r.left == node else r.left
                    if other in seen:
                        continue
                    side = r.metrics.right if other == r.right else r.metrics.left
                    if side.max_per_key != 1:
                        found.append(r)
                    seen.add(other)
                    frontier.append(other)
        return found

    def _check_repeats(self, spec, table_of, tables, edges):
        """The table whose rows are aggregated, after checking the JOIN does not repeat them."""
        agg = spec.aggregation
        if agg in SAFE_WITH_REPEATS:
            return next((table_of[c] for c in (spec.target.columns if spec.target else ())), None)
        if spec.target is None:                     # COUNT(*) / percentage of rows: which rows?
            counted = self.counted()
            if counted not in tables:
                free = [t for t in tables if not self._repeats(t, edges)]
                counted = free[0] if len(free) == 1 else self._ask(
                    "table:count", "Seleccione de que tabla se cuentan los registros.", [(t, t) for t in tables])
            self._refuse_repeated(counted, edges, "el conteo de registros", spec)
            return counted
        metric_tables = list(dict.fromkeys(table_of[c] for c in spec.target.columns))
        safe = [t for t in metric_tables if not self._repeats(t, edges)]
        if not safe:
            self._refuse_repeated(metric_tables[0], edges, f"{_AGG_NAME.get(agg, agg)} de {spec.target.label()}", spec)
        return safe[0]

    def _refuse_repeated(self, table, edges, what, spec):
        repeated = self._repeats(table, edges)
        if not repeated:
            return
        r = repeated[0]
        card = r.metrics.cardinality
        if card == "N:M":
            reason = (f"La relacion {r.label()} es N:M: el JOIN repite filas de ambas tablas y el resultado de "
                      f"{what} quedaria inflado. Una relacion N:M no es segura para agregar.")
        else:
            other = r.right if r.left == table else r.left
            reason = (f"Los datos de {table} se repiten al unirlos con {other} (relacion {r.label()}, {card}): "
                      f"cada registro de {table} aparece una vez por cada registro relacionado de {other} "
                      f"y el resultado de {what} quedaria inflado.")
        _refuse(reason + " No se genera una respuesta que podria ser incorrecta.",
                [f"una pregunta sobre {table} sin combinarla con {', '.join(t for t in spec_tables(edges) if t != table)}",
                 "o una operacion que no cambie con repeticiones (maximo, minimo o conteo de valores distintos)"])

    def _keep_base(self, spec, table_of, tables):
        """'... incluyendo los que no tienen X': the grouped table is kept whole (LEFT JOIN)."""
        group = table_of.get(spec.group_by) if spec.group_by else None
        if len(tables) != 2 or group is None:
            _refuse("Para incluir los registros sin correspondencia la pregunta debe agrupar por una "
                    "columna de una tabla y combinarla con exactamente otra tabla.",
                    ["por ejemplo: 'cuantos pedidos tiene cada cliente, incluyendo los que no tienen pedidos'"])
        return group

    # --- LEFT ANTI JOIN -------------------------------------------------------------------

    def anti(self):
        for a in self.catalog:
            for b in self.catalog:
                # up to 4 words between them: "clientes de Bogota no tienen pedidos" (they become filters)
                link = self._words(a.alias) + r"(?:\s+[^\s?]+){0,4}?" + lx.ANTI_LINK + self._words(b.alias)
                if a is not b and re.search(link, self.text):
                    return self._anti_spec(a.alias, b.alias)
        return None

    def _filters(self, tables):
        """Conditions and category values of a 'sin X' question, qualified; refused when not understood."""
        run = _Run(self.interpreter, self.parsed, self.extra)
        run.mentions, masked = run._scan(self.text, self.interpreter.index)
        masked, conditions = run._conditions(masked)
        filters = conditions + run._value_filters() + run._unexplained_values()
        self.filter_notes = run.notes
        leftover = next((m for m in run.mentions if not m.used and run._kind(m) == "numeric"), None)
        if leftover is not None:
            _refuse(f"No se entendio la condicion sobre '{leftover.word}'.",
                    [f"la condicion completa (por ejemplo: '{leftover.word} mayor a 10')"])
        if self.interpreter.profile.date_columns and re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\d{1,2}/\d{1,2}/\d{4}", self.text):
            _refuse("Las fechas todavia no se combinan con una pregunta de registros sin correspondencia.",
                    ["la pregunta sin la fecha, o una consulta SQL manual"])
        table_of = {c: self._table_of(c) for f in filters for c in (f.column, f.other_column) if c}
        outside = [c for c, t in table_of.items() if t not in tables]
        if outside:
            _refuse(f"La condicion sobre '{outside[0]}' es de la tabla {table_of[outside[0]]}, que no participa "
                    f"en la pregunta ({' y '.join(tables)}).")
        q = {c: f"{t}.{c}" for c, t in table_of.items()}
        return [dataclasses.replace(f, column=q[f.column], other_column=q.get(f.other_column) if f.other_column else None)
                for f in filters]

    def _anti_spec(self, a, b):
        path = self.path(a, b)
        if len(path) != 1:
            _refuse(f"Para buscar registros de {a} sin {b} se necesita una relacion directa entre las dos tablas.", [HINT])
        r = path[0]
        a_col, b_col = (r.left_column, r.right_column) if r.left == a else (r.right_column, r.left_column)
        join = Join("ANTI", b, f"{a}.{a_col}", f"{b}.{b_col}", r.label())
        p = self.parsed
        count = re.search(r"(?<![\w@])(?:cuant[oa]s|numero\s+de|cantidad\s+de|how\s+many)\s+(?:los\s+|las\s+)?"
                          + self._words(a), self.text) or (p.kind == s.TRUE_FALSE and p.claim is not None)
        # filters on `a` go to WHERE, filters on `b` to the ON of the ANTI JOIN (builder): "sin pedidos de Laptop"
        filters = self._filters((a, b))
        if count:
            spec = QuerySpec(s.COUNT_WHERE, s.SCALAR, "COUNT", base_table=a, joins=[join], filters=filters)
        else:
            spec = QuerySpec(s.FILTER_ROWS, s.ROWS, None, base_table=a, joins=[join], filters=filters)
        spec.question_type, spec.options, spec.claim = p.kind, list(p.options), p.claim
        spec.claim_op = getattr(p, "claim_op", "=") or "="
        spec.notes = list(self.filter_notes)
        return spec


_AGG_NAME = {"SUM": "la suma", "AVG": "el promedio", "COUNT": "el conteo", "MEDIAN": "la mediana",
             "PERCENTILE": "el percentil", "STDDEV": "la desviacion estandar", "VARIANCE": "la varianza",
             "PERCENT": "el porcentaje", "PERIOD_CHANGE": "la variacion del periodo"}


def spec_tables(edges):
    return list(dict.fromkeys(t for r in edges for t in (r.left, r.right)))


def _qualify(spec, q):
    """Every column of the spec -> 'alias.column' (the HAVING placeholder 'value' is not a column)."""
    def f(c):
        return q.get(c, c) if c else c

    def cond(c):
        return dataclasses.replace(c, column=f(c.column), other_column=f(c.other_column))

    if spec.target is not None:
        spec.target = dataclasses.replace(spec.target, columns=tuple(f(c) for c in spec.target.columns))
    spec.group_by, spec.period_column = f(spec.group_by), f(spec.period_column)
    spec.filters = [cond(c) for c in spec.filters]
    spec.percent_filters = [cond(c) for c in spec.percent_filters]
    spec.date_filters = [dataclasses.replace(d, column=f(d.column)) for d in spec.date_filters]
    spec.comparison = tuple(f(c) for c in spec.comparison) if spec.comparison else None
    spec.columns = [f(c) for c in spec.columns]
    if spec.answer.startswith("column:"):
        spec.answer = "column:" + f(spec.answer.split(":", 1)[1])
