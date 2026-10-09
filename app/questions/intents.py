"""Deterministic interpreter: ParsedQuestion -> QuerySpec (or NeedsInput).

No AI and no Internet: a vocabulary (lexicon.py) plus the dataset schema (profile and
semantic roles). Whenever something cannot be decided safely (two plausible columns,
unknown operation, ambiguous number) it returns NeedsInput instead of guessing.

Pipeline on the normalized text:
  1. dates      -> placeholders @dN@  (then date filters)
  2. mentions   -> placeholders @mN@  (columns, roles, generic words, category values)
  3. conditions -> placeholders @cN@  (column OP value / column OP column)
  4. keywords   -> aggregation, superlative, grouping, record, percentage...
  5. QuerySpec
"""
import calendar
import datetime
import re
from dataclasses import dataclass, field

import config
from app.query import spec as s
from app.query.spec import Condition, DateFilter, Metric, QuerySpec
from app.questions import lexicon as lx
from app.questions.numbers import format_number, read_number
from app.schema.profiler import ID_TOKENS
from app.text import STOPWORDS, name_tokens, normalize, phrase_pattern

PRODUCT = r"@m(\d+)@\s*(?:\*|×|x)\s*@m(\d+)@"      # explicit formula 'quantity * unit_price'
AGG_LABELS = {"SUM": "Suma total (SUM)", "AVG": "Promedio (AVG)", "MAX": "Maximo (MAX)",
              "MIN": "Minimo (MIN)", "COUNT": "Conteo de registros (COUNT)"}


@dataclass
class NeedsInput:
    """The interpreter needs a decision from the user (or cannot continue)."""
    key: str
    message: str
    options: list = field(default_factory=list)   # [(label, value)]; empty = cannot continue
    remember: bool = False                        # column choices are remembered for the session
    missing: list = field(default_factory=list)   # what the system needs to answer


class _Need(Exception):
    def __init__(self, need):
        super().__init__(need.message)
        self.need = need


@dataclass
class Mention:
    kind: str            # column | role | op_role | generic | value
    target: str          # column name, role name, generic word or value column
    start: int
    end: int
    word: str = ""
    value: object = None
    column: str = None   # resolved column
    used: bool = False
    metric: object = None   # resolved Metric of a revenue word (a column or a formula)
    choices: list = None    # value found in several columns: [(column, value)] (asked, never guessed)


class SchemaIndex:
    """Patterns that find columns, roles, generic words and category values in a question."""

    def __init__(self, profile, with_op_aliases=False):
        self.patterns = []
        for col in profile.columns:
            tokens = name_tokens(col.name)
            if tokens and not (len(tokens) == 1 and (len(tokens[0]) < 2 or tokens[0] in STOPWORDS)):
                self._add(phrase_pattern(tokens), "column", col.name, 3)
        for role, conf in config.SEMANTIC_ROLES.items():
            for alias in conf["aliases"]:
                words = alias.split()
                if alias in lx.ROLE_WORDS_EXCLUDED:
                    continue
                if lx.OPERATION_WORDS & set(words):
                    if not (with_op_aliases and words[0] in lx.OP_ALIAS_HEADS):
                        continue
                    self._add(phrase_pattern(words), "op_role", role, 1)
                else:
                    self._add(phrase_pattern(words), "role", role, 1)
        for word in lx.GENERIC_WORDS:
            self._add(rf"(?<![\w@]){word}(?![\w@])", "generic", word, 0)
        for col in profile.categorical:
            for value in col.values:
                text = normalize(str(value))
                if isinstance(value, str) and len(text) >= 2 and text not in STOPWORDS:
                    self._add(rf"(?<![\w@]){_inflected(text)}(?![\w@])", "value", col.name, 2, value)
        # Names and codes of value-indexed columns ('Ana', 'Jose Perez'): exact lookup of the
        # normalized words, never an ordinary word of a question nor a bare number.
        self.labels, self.label_words = {}, 1
        vocabulary = _vocabulary()
        for col in profile.columns:
            if col.is_categorical or not col.values:
                continue
            for value in col.values:
                text = normalize(str(value))
                if (isinstance(value, str) and len(text) >= 2 and text not in vocabulary
                        and not re.fullmatch(r"[\d\s.,%-]+", text)):
                    self.labels.setdefault(text, []).append((col.name, value))
                    self.label_words = max(self.label_words, min(len(text.split()), 6))

    def _add(self, pattern, kind, target, priority, value=None):
        self.patterns.append((re.compile(pattern), kind, target, priority, value))

    def scan(self, text):
        found = []
        for order, (rx, kind, target, priority, value) in enumerate(self.patterns):
            for m in rx.finditer(text):
                found.append(((-(m.end() - m.start()), -priority, m.start(), order),
                              Mention(kind, target, m.start(), m.end(), m.group(0), value)))
        words = list(re.finditer(r"\S+", text))
        for i in range(len(words)):
            for n in range(1, self.label_words + 1):
                chunk = words[i:i + n]
                if len(chunk) < n or any("@" in w.group(0) for w in chunk):
                    break
                start, end = chunk[0].start(), chunk[-1].end()
                for column, value in self.labels.get(" ".join(w.group(0) for w in chunk), ()):
                    found.append(((-(end - start), -2, start, len(self.patterns)),
                                  Mention("value", column, start, end, text[start:end], value)))
        # The same words found as a value of several columns: one mention that asks which column.
        spans = {}
        for key, men in found:
            if men.kind == "value":
                spans.setdefault((men.start, men.end), []).append((key, men))
        found = [(key, men) for key, men in found if men.kind != "value"]
        for items in spans.values():
            key, men = min(items, key=lambda t: t[0])
            options = list(dict.fromkeys((m.target, m.value) for _, m in items))
            if len(options) > 1:
                men.choices = options
            found.append((key, men))
        chosen = []
        for _, men in sorted(found, key=lambda t: t[0]):   # longest, then most specific
            if all(men.end <= c.start or men.start >= c.end for c in chosen):
                chosen.append(men)
        return sorted(chosen, key=lambda m: m.start)


def _inflected(text):
    """Regex for a category value and its Spanish plural/gender forms:
    'entregado' also matches 'entregados', 'entregada', 'entregadas'; 'bogota' only itself."""
    body = re.escape(text)
    if len(text) > 4 and re.search(r"[a-z]$", text) and " " not in text[-4:]:
        if text[-1] in "oa":
            return body[:-1] + "(?:o|a|os|as)"
        if text[-1] in "lrnzd":       # 'canal' -> 'canales', 'proveedor' -> 'proveedores'
            return body + "(?:es)?"
        if text[-1] == "e":
            return body + "s?"
    return body


def _vocabulary():
    """Ordinary question words (never a value): stopwords, lexicon words, role aliases, months..."""
    global _VOCABULARY
    if _VOCABULARY is None:
        words = set(STOPWORDS) | set(lx.QUESTION_WORDS) | set(lx.MONTHS) | set(lx.TIME_WORDS) | ID_TOKENS
        for name in dir(lx):
            value = getattr(lx, name)
            texts = [value] if isinstance(value, str) else (
                [t for item in value for t in (item if isinstance(item, tuple) else (item,)) if isinstance(t, str)]
                if isinstance(value, (list, set, dict)) else [])
            for text in texts:
                words.update(re.findall(r"[a-z]{2,}", text))
        for conf in config.SEMANTIC_ROLES.values():
            for alias in conf["aliases"]:
                words.update(alias.split())
        _VOCABULARY = frozenset(words)
    return _VOCABULARY


_VOCABULARY = None
_TOKEN = re.compile(r"[¿?¡!.:;\n]|[^\W\d_][\w]*")


def proper_phrases(body, mentions, known=()):
    """Capitalized words of the question that no mention explained ('Ana', 'Ciudad Gotica'),
    grouped when consecutive. A sentence's first word and ordinary words never count."""
    covered = {w for m in mentions for w in normalize(m.word).split()}
    vocabulary = _vocabulary()
    phrases, current, start = [], [], True
    for m in _TOKEN.finditer(body or ""):
        word = m.group(0)
        if not word[0].isalpha():                     # ¿ ? . ! : ; or a new line: a new sentence starts
            start = True
            phrases, current = phrases + ([current] if current else []), []
            continue
        norm = normalize(word)
        if start or not word[0].isupper() or norm in vocabulary or norm in covered or norm in known:
            phrases, current = phrases + ([current] if current else []), []
        else:
            current.append(word)
        start = False
    phrases += [current] if current else []
    return [" ".join(p) for p in phrases]


class Interpreter:
    def __init__(self, profile, semantic, choices=None, value_lookup=None, known_words=(), table_columns=None,
                 column_tables=None):
        self.profile = profile
        self.semantic = semantic
        self.value_lookup = value_lookup     # ValueLookup (Spark) for values not kept in memory, or None
        self.known_words = set(known_words)  # more ordinary words (table names in catalog mode)
        self.table_columns = table_columns or {}   # catalog: 'cliente' -> columns of the table clientes
        self.column_tables = column_tables or {}   # catalog: column -> datasets that have it (shown in choices)
        self.index = SchemaIndex(profile)
        self.index_op = SchemaIndex(profile, with_op_aliases=True)
        self.choices = choices if choices is not None else {}   # session memory (columns)

    def interpret(self, parsed, extra_choices=None):
        try:
            return _Run(self, parsed, extra_choices or {}).build()
        except _Need as need:
            return need.need


def unrecognized(missing):
    return NeedsInput("unrecognized", "Pregunta no reconocida automaticamente.", [], missing=missing)


class _Run:
    """Interpretation of one question."""

    def __init__(self, interpreter, parsed, extra):
        self.it = interpreter
        self.p = parsed
        self.extra = extra
        self.profile = interpreter.profile
        self.semantic = interpreter.semantic
        self.percentile = None
        self.time_group = None
        self.having = None
        self.notes = []          # decisions behind the answer (go to the evidence warnings)

    # --- choices and column resolution -----------------------------------------

    def _choice(self, key):
        return self.extra.get(key, self.it.choices.get(key))

    def _ask(self, key, message, options, remember=False):
        chosen = self._choice(key)
        if chosen is not None:
            return chosen
        raise _Need(NeedsInput(key, message, options, remember=remember))

    def _columns_of_kind(self, kind):
        if kind == "numeric":   # identifiers are numbers but never a metric
            return [c.name for c in self.profile.columns if c.kind == "numeric" and not c.is_id]
        if kind == "date":
            return [c.name for c in self.profile.date_columns]
        if kind == "text":
            return [c.name for c in self.profile.columns if c.kind in ("text", "boolean") and not c.is_date]
        return [c.name for c in self.profile.columns]

    def _role_column(self, role, word):
        key = f"col:{role}"
        if self._choice(key):
            return self._choice(key)
        tied = self.semantic.tied(role)
        if len(tied) == 1:
            return tied[0].column
        if tied:
            return self._ask(key, f"Se encontraron varias columnas posibles para '{word}'. Seleccione una.",
                             [(f"{c.column}  ({c.reason}){self._where(c.column)}", c.column) for c in tied], remember=True)
        options = [(c + self._where(c), c) for c in self._columns_of_kind(config.SEMANTIC_ROLES[role]["kind"])]
        if not options:
            raise _Need(NeedsInput(key, f"El dataset no tiene ninguna columna compatible con '{word}'.",
                                   missing=[f"una columna para '{word}'"]))
        return self._ask(key, f"No se encontro una columna para '{word}'. Seleccione una.", options, remember=True)

    def _generic_column(self, word):
        key = f"col:generic:{word}"
        if self._choice(key):
            return self._choice(key)
        names = []
        for role in lx.GENERIC_WORDS[word]:
            names += [c.column for c in self.semantic.of(role) if c.column not in names]
        if len(names) == 1:
            return names[0]
        options = [(n + self._where(n), n) for n in (names or self._columns_of_kind("numeric"))]
        message = (f"Se encontraron varias columnas posibles para '{word}'. Seleccione una." if names
                   else f"No se encontro una columna para '{word}'. Seleccione una.")
        return self._ask(key, message, options, remember=True)

    def _where(self, column):
        """'  [dataset: ventas]' in catalog mode: a choice between columns shows where each one is."""
        tables = self.it.column_tables.get(column)
        return f"  [dataset: {', '.join(tables)}]" if tables else ""

    def _is_revenue(self, m):
        return m.kind == "generic" and m.target in lx.REVENUE_WORDS

    def _revenue_metric(self, m):
        """'ingresos' / 'ventas': an existing column or a formula. Returns a Metric, or
        "COUNT" when the user says 'ventas' means number of sales. Never chosen silently
        when there is more than one reading; a formula is always confirmed."""
        if m.metric is not None:
            return m.metric
        countable = m.target in lx.COUNTABLE_WORDS
        key = f"metric:{'ventas' if countable else 'ingresos'}"
        options = []
        for role in lx.GENERIC_WORDS[m.target]:
            for c in self.semantic.of(role):
                if c.column not in [o[1].columns[0] for o in options]:
                    options.append((f"Columna {c.column}", Metric("column", (c.column,))))
        columns_found = len(options)
        quantity = self._resolve_role_silently("QUANTITY")
        if quantity:
            taken = {o[1].columns[0] for o in options}
            prices = [c.column for c in self.semantic.of("PRICE") if c.column not in taken and c.column != quantity]
            discount = self._resolve_role_silently("DISCOUNT")
            scale = self._discount_scale(discount)
            for price in prices[:3]:
                metric = Metric("product", (quantity, price))
                options.append((f"{metric.label()}  (cantidad x precio)", metric))
                if discount and scale:
                    metric = Metric("product_discount", (quantity, price, discount), scale)
                    options.append((f"{metric.label()}  (con descuento)", metric))
        if countable:
            options.append(("Numero de ventas (conteo de registros)", "COUNT"))
        if not options:
            raise _Need(NeedsInput(key, f"No se encontro una columna ni una formula para '{m.word}'.",
                                   missing=[f"una columna de {m.word} (o cantidad y precio para calcularlo)"]))
        if len(options) == 1 and columns_found == 1:
            m.metric = options[0][1]
        else:
            m.metric = self._ask(key, f"METRICA AMBIGUA: se detecto '{m.word}'. Seleccione la interpretacion "
                                      "indicada por el taller.", options, remember=True)
        return m.metric

    def _discount_scale(self, column):
        """1 when the discount is stored as 0.15, 100 when stored as 15; None if unclear."""
        if not column:
            return None
        col = self.profile.column(column)
        top = col.max if col is not None else None
        if top is None:
            return None
        return 1 if top <= 1 else (100 if top <= 100 else None)

    def _metric(self, m):
        """Metric of a numeric mention (a revenue word may be a formula)."""
        if self._is_revenue(m):
            metric = self._revenue_metric(m)
            if metric == "COUNT":
                raise _Need(unrecognized(["una medida numerica: 'ventas' se eligio como conteo de registros"]))
            return metric
        return Metric("column", (self._resolve(m),))

    def _resolve(self, m):
        if m.column is None:
            if m.kind in ("column", "value"):
                m.column = m.target
            elif m.kind in ("role", "op_role"):
                m.column = self._role_column(m.target, m.word)
            else:
                m.column = self._generic_column(m.target)
        return m.column

    def _kind(self, m):
        if m.kind == "value":
            return "value"
        if m.kind == "generic":
            return "numeric"
        if m.kind in ("role", "op_role"):
            return config.SEMANTIC_ROLES[m.target]["kind"]
        col = self.profile.column(m.target)
        if col.is_id and col.kind == "numeric":
            return "any"        # 'cliente_id' groups and filters, but it is never a measure to sum
        return "date" if col.is_date else ("text" if col.kind == "boolean" else col.kind)

    def _resolve_role_silently(self, role):
        """Column for a role when it is unambiguous or already chosen; None otherwise."""
        return self._choice(f"col:{role}") or self.semantic.resolved(role)

    # --- main ------------------------------------------------------------------

    def build(self):
        text = self.p.normalized
        if not text:
            raise _Need(unrecognized(["el texto de la pregunta"]))
        self.inferred_claim = None
        text = re.sub(lx.COUNT_ROWS_PHRASE, lambda m: m.group(0) if self._measure_total(text, m) else "cuantos registros", text)
        text, dates = self._extract_dates(text)
        self.mentions, masked = self._scan(text, self.it.index_op)
        if any(m.kind == "op_role" for m in self.mentions) and not any(
                re.search(rx, masked) for rx in lx.OPERATION_KEYWORDS):
            # 'precio maximo' with no other operation word: 'maximo' IS the operation.
            self.mentions, masked = self._scan(text, self.it.index)
        masked, date_filters = self._date_filters(masked, dates)
        masked, self.time_group = self._time_group(masked)
        masked, self.having = self._having(masked)
        masked, conditions = self._conditions(masked)
        self.masked = masked
        spec = self._shape(masked, conditions, date_filters)
        if self.having and spec.shape not in (s.GROUP, s.GROUPS):
            raise _Need(unrecognized(["la agrupacion de la condicion (por ejemplo: 'empresas con un promedio "
                                      "de cierre mayor a 100')"]))
        spec.question_type = self.p.kind
        spec.options = list(self.p.options)
        spec.claim = self.p.claim if self.p.claim is not None else self.inferred_claim
        spec.claim_op = getattr(self.p, "claim_op", "=") or "="
        spec.notes = list(self.notes) + spec.notes
        if spec.question_type == s.TRUE_FALSE and spec.claim is None:
            raise _Need(unrecognized(["el valor afirmado (por ejemplo: '... es 15' o '... es mayor a 100')"]))
        return spec

    # --- time grouping and HAVING ------------------------------------------------

    def _time_group(self, masked):
        """'por mes' / 'en que ano' / 'mensual' -> (grain, source) and the words removed."""
        if not self.profile.date_columns:
            return masked, None
        for rx, source in lx.TIME_GROUP:
            m = re.search(rx, masked)
            if m:
                return masked[:m.start()] + " " + masked[m.end():], (lx.TIME_WORDS[m.group(1)], source)
        return masked, None

    def _having(self, masked):
        """'con mas de 100 pedidos' / 'con un promedio de cierre mayor a 100' -> HAVING."""
        m = re.search(lx.HAVING_COUNT, masked)
        if m:
            op = lx.HAVING_COUNT_OPS[" ".join(m.group(1).split())]
            having = {"agg": "COUNT", "mention": None, "op": op, "value": self._number(m.group(2))}
            return masked[:m.start()] + " " + masked[m.end():], having
        ops = "|".join(f"(?:{rx})" for rx, _ in lx.OPERATORS)
        aggs = "|".join(lx.HAVING_AGG_WORDS)
        number = r"(?P<num>-?\d[\d.,]*\d|-?\d)(?![\d@])"
        for rx in (rf"(?<![\w@])(?P<agg>{aggs})\s+(?:de\s+(?:la\s+|el\s+|los\s+|las\s+)?)?@m(?P<m>\d+)@{lx.COPULA}\s*"
                   rf"(?P<op>{ops}){lx.ARTICLES}\s*{number}",
                   rf"@m(?P<m>\d+)@\s+(?P<agg>{aggs}){lx.COPULA}\s*(?P<op>{ops}){lx.ARTICLES}\s*{number}"):
            m = re.search(rx, masked)
            if not m:
                continue
            mention = self.mentions[int(m.group("m"))]
            if self._kind(mention) != "numeric":
                continue
            op = next(sql for rx_op, sql in lx.OPERATORS if re.fullmatch(rx_op, m.group("op")))
            mention.used = True
            having = {"agg": lx.HAVING_AGG_WORDS[m.group("agg")], "mention": mention, "op": op,
                      "value": self._number(m.group("num"))}
            return masked[:m.start()] + " " + masked[m.end():], having
        return masked, None

    @staticmethod
    def _scan(text, index):
        mentions = index.scan(text)
        masked = text
        for i in reversed(range(len(mentions))):
            m = mentions[i]
            masked = f"{masked[:m.start]}@m{i}@{masked[m.end:]}"
        return mentions, masked

    # --- dates -----------------------------------------------------------------

    def _extract_dates(self, text):
        """Replaces dates, months and years by @dN@ placeholders: [(first_day, last_day)]."""
        dates = []
        if not self.profile.date_columns:
            return text, dates

        def put(first, last):
            dates.append((first, last))
            return f"@d{len(dates) - 1}@"

        def iso(m):
            return put(*([_safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))] * 2))

        def dmy(m):
            return put(*([_safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))] * 2))

        def month_year(m):
            month, year = lx.MONTHS[m.group(1)], int(m.group(2))
            return put(datetime.date(year, month, 1), datetime.date(year, month, calendar.monthrange(year, month)[1]))

        def year(m):
            y = int(m.group(2))
            return m.group(1) + put(datetime.date(y, 1, 1), datetime.date(y, 12, 31))

        text = re.sub(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", iso, text)
        text = re.sub(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", dmy, text)
        text = re.sub(rf"(?<![\w@])({'|'.join(lx.MONTHS)})\s+(?:de\s+|del\s+)?((?:19|20)\d{{2}})(?!\d)", month_year, text)
        prepositions = (r"en\s+el\s+ano|en|durante|del\s+ano|el\s+ano|ano|in|during|year|desde|hasta|entre|"
                        r"antes\s+del?|despues\s+del?|a\s+partir\s+del?|from|since|until|before|after|between")
        text = re.sub(rf"((?<![\w@])(?:{prepositions})\s+)((?:19|20)\d{{2}})(?!\d)", year, text)
        text = re.sub(r"(@d\d+@\s+(?:y|al|a|hasta|and|to)\s+)((?:19|20)\d{2})(?!\d)", year, text)
        if None in [d for pair in dates for d in pair]:
            raise _Need(unrecognized(["una fecha valida (aaaa-mm-dd o dd/mm/aaaa)"]))
        return text, dates

    def _date_filters(self, masked, dates):
        if not dates:
            return masked, []
        filters = []

        def range_(m):
            filters.append((">=", dates[int(m.group(1))][0]))
            filters.append(("<=", dates[int(m.group(2))][1]))
            return " "

        def rule(op, side):
            def apply(m):
                filters.append((op, dates[int(m.group(1))][side]))
                return " "
            return apply

        art = r"\s+(?:el\s+|la\s+)?"
        masked = re.sub(rf"(?:entre|desde|del|de|between|from){art}@d(\d+)@\s+(?:y|hasta|al|a|and|to){art}@d(\d+)@", range_, masked)
        masked = re.sub(rf"(?:antes\s+del?|before){art}@d(\d+)@", rule("<", 0), masked)
        masked = re.sub(rf"(?:hasta|until){art}@d(\d+)@", rule("<=", 1), masked)
        masked = re.sub(rf"(?:despues\s+del?|after){art}@d(\d+)@", rule(">", 1), masked)
        masked = re.sub(rf"(?:a\s+partir\s+del?|desde|since){art}@d(\d+)@", rule(">=", 0), masked)

        def single(m):
            first, last = dates[int(m.group(1))]
            if first == last:
                filters.append(("=", first))
            else:
                filters.extend([(">=", first), ("<=", last)])
            return " "

        masked = re.sub(r"@d(\d+)@", single, masked)
        column = self._role_column("DATE", "fecha")
        col = self.profile.column(column)
        kind = "text" if col.kind == "text" else ("timestamp" if col.dtype.startswith("timestamp") else "date")
        return masked, [DateFilter(column, op, d.isoformat(), kind, col.date_format) for op, d in filters]

    # --- conditions ------------------------------------------------------------

    def _conditions(self, masked):
        ops = "|".join(f"(?:{rx})" for rx, _ in lx.OPERATORS)
        words = "|".join(lx.NUMBER_WORDS)
        pattern = re.compile(rf"@m(\d+)@{lx.COPULA}\s*(?P<op>{ops}){lx.ARTICLES}\s*"
                             rf"(?:@m(?P<right>\d+)@|(?P<num>-?\d[\d.,]*\d|-?\d)|(?P<word>{words})(?!\w))(?![\d@])")
        conditions = []

        def between(m):
            left = self.mentions[int(m.group(1))]
            if self._kind(left) != "numeric" or left.used or self._is_revenue(left):
                return m.group(0)
            column = self._resolve(left)
            left.used = True
            conditions.append(Condition(column, ">=", value=self._number(m.group(2))))
            conditions.append(Condition(column, "<=", value=self._number(m.group(3))))
            return f" @c{len(conditions) - 2}@ @c{len(conditions) - 1}@ "

        num = r"(-?\d[\d.,]*\d|-?\d)"
        masked = re.sub(rf"@m(\d+)@{lx.COPULA}\s*(?:entre|between)\s+{num}\s+(?:y|and|a)\s+{num}(?![\d@])",
                        between, masked)

        def replace(m):
            left = self.mentions[int(m.group(1))]
            if self._kind(left) not in ("numeric", "any") or left.used or self._is_revenue(left):
                return m.group(0)
            op = next(sql for rx, sql in lx.OPERATORS if re.fullmatch(rx, m.group("op")))
            if m.group("right") is not None:
                right = self.mentions[int(m.group("right"))]
                if self._kind(right) != "numeric" or self._is_revenue(right):
                    return m.group(0)
                cond = Condition(self._resolve(left), op, other_column=self._resolve(right))
                right.used = True
            elif m.group("word"):       # "mayor que cero" -> > 0 (the operator is kept as written)
                cond = Condition(self._resolve(left), op, value=lx.NUMBER_WORDS[m.group("word")])
            else:
                cond = Condition(self._resolve(left), op, value=self._number(m.group("num")))
            left.used = True
            conditions.append(cond)
            return f" @c{len(conditions) - 1}@ "

        return pattern.sub(replace, masked), conditions

    def _number(self, text):
        readings = read_number(text)
        if len(readings) == 1:
            value = readings[0].value
        else:
            value = self._ask(f"num:{text}", f"El numero '{text}' es ambiguo. Seleccione su valor.",
                              [(f"{text} = {format_number(r.value, 6)}", r.value) for r in readings])
        return int(value) if float(value).is_integer() else value

    # --- shape -----------------------------------------------------------------

    def _shape(self, masked, conditions, date_filters):
        def has(rx):
            return re.search(rx, masked) is not None

        # "cuantas unidades se vendieron": a count of units is their TOTAL, not the number of rows.
        masked = re.sub(lx.COUNT_UNITS, lambda m: f"total {m.group(1)}" if self._units_at(m) else m.group(0), masked)
        count_word = has(lx.COUNT) or has(lx.MORE_ROWS) or (
            self.p.kind == s.TRUE_FALSE and has(lx.ROWS) and not (has(lx.AVG) or has(lx.SUM) or has(lx.MAX) or has(lx.MIN)))
        # "mas unidades" / "menos pedidos": a superlative on a numeric mention (a larger TOTAL).
        more = [m for m in re.finditer(lx.MORE_METRIC, masked) if self._numeric_at(m)]
        less = [m for m in re.finditer(lx.LESS_METRIC, masked) if self._numeric_at(m)]
        # "mayor cantidad de unidades": per group, a count of units is a TOTAL (unless "un solo pedido").
        units = not has(lx.RECORD) and any(self._units_at(m) for m in re.finditer(lx.SUPERLATIVE_METRIC, masked))
        marks = [(m.start(), "DESC") for m in re.finditer(lx.MAX, masked)] + \
                [(m.start(), "ASC") for m in re.finditer(lx.MIN, masked)] + \
                [(m.start(), "DESC") for m in re.finditer(lx.MORE_ROWS, masked)] + \
                [(m.start(), "DESC") for m in more] + [(m.start(), "ASC") for m in less]
        order = min(marks)[1] if marks else None
        pct_change, period = has(lx.PCT_CHANGE), has(lx.PERIOD)
        percent = has(lx.PERCENT) and not pct_change

        # 'ventas' may mean the number of sales: asked once, before deciding what to count.
        if not count_word:
            for m in self.mentions:
                if not m.used and self._is_revenue(m) and m.target in lx.COUNTABLE_WORDS \
                        and not re.search(PRODUCT, masked):
                    if self._revenue_metric(m) == "COUNT":
                        m.used = True
                        count_word = True
                    break

        entity, entity_source, ask_date = self._entity(masked)
        group = {}
        if self.time_group:
            grain, source = self.time_group
            if entity is not None:
                raise _Need(unrecognized(["una sola agrupacion (por ejemplo 'por mes' o 'por empresa', no ambas)"]))
            entity = self._role_column("DATE", "fecha")
            entity_source = source
            col = self.profile.column(entity)
            kind = "text" if col.kind == "text" else ("timestamp" if col.dtype.startswith("timestamp") else "date")
            group = dict(group_time=grain, group_date_kind=kind, group_date_format=col.date_format,
                         cumulative=has(lx.CUMULATIVE))
        if entity is None and not group:
            entity = self._named_group(masked)       # "por vendedor": a grouping is never dropped (F1)
            entity_source = "group" if entity else entity_source
        if has(r"(?<![\w@])(?:cuando|when)(?![\w@])"):
            ask_date = True
        filters = conditions + self._value_filters() + self._unexplained_values()

        # 'Bogota o Medellin', 'Bogota tiene mas pedidos que Medellin': compare those groups.
        if entity is None and order:
            several = next((f for f in filters if f.op == "IN"), None)
            if several is not None:
                entity, entity_source = several.column, "which"
                self.inferred_claim = str(several.value[0])

        top_n = re.search(lx.TOP_N, masked)
        limit = int(top_n.group(1)) if top_n else None

        base = dict(filters=filters, date_filters=date_filters, order=order, limit=limit, **group)

        # HAVING: 'empresas con un promedio de cierre mayor a 100', 'ciudades con mas de 10 pedidos'
        if self.having:
            if entity is None:
                m = re.search(r"(?<![\w@])(?:cuant[oa]s|how\s+many|que|cuales|which)\s+@m(\d+)@", masked)
                men = self.mentions[int(m.group(1))] if m else None
                if men is None or men.used or self._kind(men) not in ("text", "date", "any"):
                    raise _Need(unrecognized(["la agrupacion (por ejemplo: 'cuantas empresas tienen ...')"]))
                men.used = True
                entity = self._resolve(men)
            h = self.having
            target = self._metric(h["mention"]) if h["mention"] else None
            cond = Condition("value", h["op"], value=h["value"])
            if count_word:
                return QuerySpec(s.GROUP_COUNT, s.GROUPS, h["agg"], target, group_by=entity, having=[cond],
                                 **{**base, "order": None})
            return QuerySpec(s.GROUP_AGG, s.GROUP, h["agg"], target, group_by=entity, having=[cond], answer="label",
                             **{**base, "order": order or "DESC"})

        # Counting ---------------------------------------------------------------
        if count_word and not pct_change and not percent:
            distinct_target = None if group else self._distinct_target(masked, entity, entity_source)
            # A numeric column that no condition used: counting (also distinct values) would silently
            # drop it. Checked before the COUNT DISTINCT answer too (F3).
            leftover = next((m for m in self.mentions if not m.used and self._kind(m) == "numeric"
                             and not self._is_revenue(m)), None)
            if leftover is not None:
                raise _Need(unrecognized([f"la condicion sobre '{leftover.word}' (por ejemplo: "
                                          f"'{leftover.word} mayor a 10')"]))
            if distinct_target:
                return QuerySpec(s.COUNT_DISTINCT, s.SCALAR, "COUNT_DISTINCT", Metric("column", (distinct_target,)),
                                 **{**base, "order": None})
            if entity:
                shape = s.TOP if order else s.GROUP
                return QuerySpec(s.GROUP_TOP if shape == s.TOP else s.GROUP_AGG, shape, "COUNT", group_by=entity,
                                 answer="label" if entity_source != "group" else "value", **base)
            intent = s.COUNT_WHERE if (filters or date_filters) else s.COUNT_ROWS
            return QuerySpec(intent, s.SCALAR, "COUNT", **{**base, "order": None})

        numbers = self._numeric_mentions()
        target = self._derived_metric(masked, numbers, pct_change and not period)
        compare = has(lx.COMPARE) or self._joined_by_or(masked)
        if target is None and numbers and not compare:
            metrics = {}
            for m in numbers:
                metric = self._metric(m)
                metrics.setdefault(metric.label(), metric)
            if len(metrics) > 1:
                target = self._ask("col:metric", "Se mencionan varias columnas numericas. Seleccione la que desea analizar.",
                                   [(label + (self._where(metric.columns[0]) if metric.kind == "column" else ""), metric)
                                    for label, metric in metrics.items()])
            else:
                target = next(iter(metrics.values()))

        # Comparison of two columns -------------------------------------------------
        if compare and len(numbers) >= 2 and target is None:
            a, b = self._resolve(numbers[0]), self._resolve(numbers[1])
            agg = self._aggregation(masked, allow_superlative=False) or self._ask(
                "agg", f"Que operacion desea comparar entre {a} y {b}?", [(AGG_LABELS[k], k) for k in ("AVG", "SUM", "MAX", "MIN")])
            return QuerySpec(s.COLUMN_COMPARISON, s.COMPARE, agg, comparison=(a, b), answer="label",
                             **{**base, "order": order or "DESC"})

        # Percentage of rows / of a total ----------------------------------------
        if percent:
            if not filters:
                raise _Need(unrecognized(["la condicion del porcentaje (por ejemplo: Close > Open)"]))
            return QuerySpec(s.PERCENTAGE_OF, s.SCALAR, "PERCENT", target=target, percent_filters=filters,
                             **{**base, "filters": [], "order": None})

        # Change over a period (first vs last date) --------------------------------
        if pct_change and period:
            if target is None:
                close = self._resolve_role_silently("CLOSE")
                target = Metric("column", (close or self._ask("col:metric", "Seleccione la columna para la variacion del periodo.",
                                                              [(c, c) for c in self._columns_of_kind("numeric")]),))
            date_col = self._role_column("DATE", "fecha")
            shape = (s.TOP if order else s.GROUP) if entity else s.SCALAR
            return QuerySpec(s.PCT_CHANGE_PERIOD, shape, "PERIOD_CHANGE", target=target, group_by=entity,
                             period_column=date_col, answer="label" if entity_source in ("which", "with") else "value", **base)

        if target is None:
            if self._aggregation(masked) or order:
                options = [(c + self._where(c), c) for c in self._columns_of_kind("numeric")]
                ident = next((m for m in self.mentions if not m.used and m.kind == "column" and self._kind(m) == "any"), None)
                message = (f"'{ident.word}' es un identificador: no se suma ni se promedia. Seleccione la medida "
                           "(para contar sus valores pregunte 'cuantos ... distintos')." if ident
                           else "No se identifico la columna numerica. Seleccione una."
                           + (" Si ninguna corresponde, cancele y revise los datasets y su esquema (menu 10)."
                              if self.it.column_tables else ""))
                chosen = self._ask("col:metric", message, options)
                target = Metric("column", (chosen,))
            else:
                raise _Need(unrecognized(["la operacion (promedio, suma, maximo, minimo o conteo)",
                                          "la columna a analizar (por ejemplo: " +
                                          ", ".join(self._columns_of_kind("numeric")[:4]) + ")"]))

        intent = {"difference": s.DIFFERENCE, "pct_change": s.PCT_CHANGE}.get(target.kind)
        agg = self._aggregation(masked)
        revenue = any(self._is_revenue(m) for m in self.mentions)   # also when its formula was written
        if entity and (more or less or units or revenue) and self._aggregation(masked, allow_superlative=False) is None:
            agg = "SUM"      # "la ciudad con mas unidades" / "mayores ingresos" = the largest total
        record = has(lx.RECORD)
        extra = {"percentile": self.percentile} if agg == "PERCENTILE" else {}

        # A group (company, product, month...) is involved -------------------------------
        if entity:
            explicit_agg = agg in ("AVG", "SUM", "MEDIAN", "PERCENTILE", "STDDEV", "VARIANCE")
            if record and not explicit_agg and order and not group:
                return QuerySpec(intent or s.RECORD_EXTREME, s.RECORD, None, target, answer=f"column:{entity}", **base)
            if (agg in ("MAX", "MIN") and entity_source == "group" and not (more or less or units or revenue)
                    and not group and not top_n):
                # "el importe maximo por producto": 'por' groups, 'maximo' is the operation of each group.
                return QuerySpec(intent or s.GROUP_AGG, s.GROUP, agg, target, group_by=entity, answer="value",
                                 **{**base, "order": None}, **extra)
            if not explicit_agg:
                if order and not group:
                    options = [(AGG_LABELS["SUM"], "SUM"), (AGG_LABELS["AVG"], "AVG"),
                               ("Valor de un solo registro (registro extremo)", "RECORD")]
                elif order:
                    options = [(AGG_LABELS["SUM"], "SUM"), (AGG_LABELS["AVG"], "AVG")]
                else:
                    options = [(AGG_LABELS[k], k) for k in ("SUM", "AVG", "MAX", "MIN")]
                agg = self._ask("agg", f"Que calculo desea por {entity}?", options)
                if agg == "RECORD":
                    return QuerySpec(intent or s.RECORD_EXTREME, s.RECORD, None, target, answer=f"column:{entity}", **base)
            shape = s.TOP if order else s.GROUP
            return QuerySpec(intent or (s.GROUP_TOP if shape == s.TOP else s.GROUP_AGG), shape, agg, target,
                             group_by=entity, answer="label" if entity_source != "group" else "value", **base, **extra)

        # A single row with the extreme value -------------------------------------------
        if order and (ask_date or (record and agg not in ("AVG", "SUM"))):
            answer = "row"
            if ask_date:
                answer = f"column:{self._role_column('DATE', 'fecha')}"
            return QuerySpec(intent or s.RECORD_EXTREME, s.RECORD, None, target, answer=answer, **base)

        # One value -------------------------------------------------------------------
        if agg is None and target.kind == "column":
            # 'precio maximo' matched a column named precio_maximo: the word was the aggregation too.
            tokens = set(name_tokens(target.columns[0]))
            agg = "MAX" if tokens & {"max", "maximo", "maxima"} else ("MIN" if tokens & {"min", "minimo", "minima"} else None)
        if agg is None:
            agg = self._ask("agg", f"Que calculo desea sobre {target.label()}?",
                            [(AGG_LABELS[k], k) for k in ("AVG", "SUM", "MAX", "MIN")])
        default_intent = s.AGG_WHERE if (filters or date_filters) else s.AGG_SCALAR
        return QuerySpec(intent or default_intent, s.SCALAR, agg, target, **{**base, "order": None}, **extra)

    def _explicit_product(self, masked):
        """'quantity * unit_price' (also × or x) between two numeric columns -> Metric product."""
        m = re.search(PRODUCT, masked)
        if not m:
            return None
        a, b = self.mentions[int(m.group(1))], self.mentions[int(m.group(2))]
        if self._kind(a) != "numeric" or self._kind(b) != "numeric" or self._is_revenue(a) or self._is_revenue(b):
            return None
        a.used = b.used = True
        return Metric("product", (self._resolve(a), self._resolve(b)))

    def _numeric_at(self, match):
        """The 'mas @mN@' match points to a numeric mention."""
        men = self.mentions[int(re.search(r"@m(\d+)@", match.group(0)).group(1))]
        return self._kind(men) == "numeric"

    def _units_at(self, match):
        """The 'mayor @mN@' / 'cuantas @mN@' match points to a count of units (quantity, returned units)."""
        men = self.mentions[int(re.search(r"@m(\d+)@", match.group(0)).group(1))]
        if men.kind in ("role", "op_role"):
            return men.target in lx.UNIT_ROLES
        return men.kind == "column" and any(c.column == men.target
                                            for role in lx.UNIT_ROLES for c in self.semantic.of(role)[:1])

    # --- pieces ------------------------------------------------------------------

    def _measure_total(self, text, m):
        """'importe total de pedidos': 'total' belongs to the measure written right before it (a SUM
        over the orders), not to the orders (a count). 'cantidad / numero total de pedidos' count (F4)."""
        if not m.group(0).startswith("total"):
            return False
        before = self.it.index.scan(text[:m.start()].rstrip())
        last = before[-1] if before else None
        return (last is not None and last.end == len(text[:m.start()].rstrip()) and self._kind(last) == "numeric"
                and last.word.split()[0] not in lx.COUNT_NOUNS)

    def _entity(self, masked):
        """Grouping column: 'por empresa', 'que empresa', 'la empresa con ...'."""
        ask_date = False
        for rx, source in ((lx.GROUP_BY, "group"), (lx.WHICH, "which"), (lx.ENTITY_WITH, "with")):
            for m in re.finditer(rx, masked):
                men = self.mentions[int(m.group(1))]
                kind = self._kind(men)
                if men.used or kind in ("numeric", "value"):
                    continue
                if source == "with" and re.match(r"\s*@c\d+@", masked[m.end():]):
                    continue   # 'los productos con precio > 100': 'con' starts a filter, not a ranking (F7)
                if kind == "date" and source != "group":
                    ask_date = True
                    men.used = True
                    continue
                men.used = True
                return self._resolve(men), source, ask_date
        return None, None, ask_date

    def _value_filters(self):
        by_column = {}
        for m in self.mentions:
            if m.kind == "value" and not m.used:
                if m.choices:      # 'Luis' is a client and a referrer: asked, never guessed
                    m.target, m.value = self._ask(
                        f"value:{normalize(m.word)}", f"El valor '{m.word}' aparece en varias columnas. "
                        "Seleccione en cual se filtra.", [(f"{c} = {v}", (c, v)) for c, v in m.choices])
                m.used = True
                by_column.setdefault(m.target, [])
                if m.value not in by_column[m.target]:
                    by_column[m.target].append(m.value)
        return [Condition(col, "=", value=vals[0]) if len(vals) == 1 else Condition(col, "IN", value=vals)
                for col, vals in by_column.items()]

    def _named_group(self, masked):
        """'por vendedor' / 'cada cliente' with no column of that name: the user picks the column
        (in catalog mode, among the columns of the table named 'cliente'). Never a global total."""
        m = re.search(lx.GROUP_WORD, masked)
        if m is None:
            return None
        word = m.group("w")
        if word in lx.NOT_A_GROUP or re.fullmatch(lx.ROW_WORDS, word):    # 'por ciento', 'por pedido' (per row)
            return None
        options = self.it.table_columns.get(word) or [
            c.name for c in self.profile.columns if (c.kind in ("text", "boolean") or c.is_id or c.is_date)]
        if not options:
            raise _Need(unrecognized([f"una columna para agrupar por '{word}'"]))
        return self._ask(f"group:{word}", f"La pregunta agrupa por '{word}', pero no hay una columna con ese nombre. "
                         "Seleccione la columna para agrupar (o cancele).", [(c, c) for c in options])

    def _unexplained_values(self):
        """Capitalized words that nothing explained ('Ana'): a value found with Spark becomes a
        filter; a value in several columns is asked; a word found nowhere is asked before ignoring it."""
        filters = []
        for phrase in proper_phrases(self.p.body, self.mentions, self.it.known_words):
            norm = normalize(phrase)
            found = self.it.value_lookup(norm) if self.it.value_lookup else []
            if len(found) > 1:
                found = [self._ask(f"value:{norm}", f"El valor '{phrase}' aparece en varias columnas. "
                                   "Seleccione en cual se filtra.", [(f"{c} = {v}", (c, v)) for c, v in found])]
            if found:
                filters.append(Condition(found[0][0], "=", value=found[0][1]))
                continue
            self._ask(f"unknown:{norm}", f"'{phrase}' no coincide con ningun valor de los datos ni con una columna. "
                      "Si solo describe el contexto general del ejercicio (por ejemplo, el pais de todos los "
                      "registros), elija 1 y no se usara como filtro. Si es una condicion necesaria, cancele: no se "
                      "puede verificar con estas columnas y la pregunta no se responde.",
                      [(f"Es contexto general: responder sin filtrar por '{phrase}'", "ignore")], remember=True)
            # The decision is part of the answer: it is written in the evidence, not only asked.
            self.notes.append(f"'{phrase}' no se uso como filtro: no aparece en los datos y el usuario indico que "
                              "es contexto general del ejercicio.")
        return filters

    def _distinct_target(self, masked, entity, entity_source):
        """'cuantas empresas (distintas)' -> column; None for a plain row count."""
        m = re.search(r"(?<![\w@])(?:cuant[oa]s|how\s+many|numero\s+de|cantidad\s+de)\s+@m(\d+)@", masked)
        candidate = None
        if m:
            men = self.mentions[int(m.group(1))]
            if self._kind(men) in ("text", "date", "any") and not men.used:
                candidate = men
        if candidate is None and re.search(lx.DISTINCT, masked):
            candidate = next((x for x in self.mentions if not x.used and self._kind(x) in ("text", "date", "any", "numeric")), None)
            if candidate is None:       # 'distintos' was asked: never fall back to counting rows
                ids = [c.name for c in self.profile.columns if c.is_id and c.kind == "numeric"]
                return self._ask("col:distinct", "Se pidio contar valores distintos, pero no se identifico la "
                                 "columna. Seleccione cual.", [(c, c) for c in ids + self._columns_of_kind("text")])
        if candidate is None:
            return None
        candidate.used = True
        return self._resolve(candidate)

    def _numeric_mentions(self):
        nums = [m for m in self.mentions if not m.used and self._kind(m) == "numeric"]
        specific = [m for m in nums if m.kind != "generic"]
        return specific or nums

    def _joined_by_or(self, masked):
        return re.search(r"@m\d+@\s+(?:o|or)\s+(?:el\s+|la\s+|de\s+)?@m\d+@", masked) is not None

    def _derived_metric(self, masked, numbers, pct_change):
        if pct_change:
            if len(numbers) >= 2:
                first, second = numbers[0], numbers[1]
                between = masked[masked.find(f"@m{self.mentions.index(first)}@"):masked.find(f"@m{self.mentions.index(second)}@")]
                a, b = self._resolve(first), self._resolve(second)
                base, final = (b, a) if re.search(lx.VERSUS, between) else (a, b)
                return Metric("pct_change", (base, final))
            open_col, close_col = self._resolve_role_silently("OPEN"), self._resolve_role_silently("CLOSE")
            if open_col and close_col:
                return Metric("pct_change", (open_col, close_col))
            numeric = [(c, c) for c in self._columns_of_kind("numeric")]
            base = self._ask("pct:base", "Seleccione la columna del valor INICIAL de la variacion.", numeric)
            final = self._ask("pct:final", "Seleccione la columna del valor FINAL de la variacion.", numeric)
            return Metric("pct_change", (base, final))

        product = self._explicit_product(masked)
        if product:
            # A formula written in the question ('ingresos (quantity * unit_price)') has priority
            # over the generic ambiguity of 'ingresos' / 'ventas': those words are only its name.
            for m in self.mentions:
                if self._is_revenue(m):
                    m.used = True
            return product
        minus = re.search(r"@m(\d+)@\s*(?:-|menos|minus)\s*@m(\d+)@", masked)
        if minus:
            a, b = self.mentions[int(minus.group(1))], self.mentions[int(minus.group(2))]
            if self._kind(a) == "numeric" and self._kind(b) == "numeric":
                return Metric("difference", (self._resolve(a), self._resolve(b)))
        if re.search(lx.DIFFERENCE, masked):
            if len(numbers) >= 2:
                return Metric("difference", (self._resolve(numbers[0]), self._resolve(numbers[1])))
            high, low = self._resolve_role_silently("HIGH"), self._resolve_role_silently("LOW")
            if re.search(r"rango|amplitud|range|spread", masked) and high and low:
                return Metric("difference", (high, low))
            numeric = [(c, c) for c in self._columns_of_kind("numeric")]
            a = self._ask("diff:a", "Seleccione la PRIMERA columna de la diferencia (A en A - B).", numeric)
            b = self._ask("diff:b", "Seleccione la SEGUNDA columna de la diferencia (B en A - B).", numeric)
            return Metric("difference", (a, b))
        return None

    def _aggregation(self, masked, allow_superlative=True):
        found = [(m.start(), agg) for rx, agg in ((lx.AVG, "AVG"), (lx.SUM, "SUM"), (lx.MEDIAN, "MEDIAN"),
                                                  (lx.STDDEV, "STDDEV"), (lx.VARIANCE, "VARIANCE"))
                 for m in re.finditer(rx, masked)]
        pct = re.search(lx.PERCENTILE, masked)
        if pct and 1 <= int(pct.group(1)) <= 99:
            self.percentile = int(pct.group(1)) / 100
            found.append((pct.start(), "PERCENTILE"))
        if found:
            return min(found)[1]
        if allow_superlative:
            maxw, minw = re.search(lx.MAX, masked), re.search(lx.MIN, masked)
            if maxw or minw:
                return "MIN" if minw and (not maxw or minw.start() < maxw.start()) else "MAX"
        return None


def _safe_date(year, month, day):
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None
