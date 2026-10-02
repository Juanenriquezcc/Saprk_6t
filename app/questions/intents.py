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
from app.text import STOPWORDS, name_tokens, normalize, phrase_pattern

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
                    self._add(rf"(?<![\w@]){re.escape(text)}(?![\w@])", "value", col.name, 2, value)

    def _add(self, pattern, kind, target, priority, value=None):
        self.patterns.append((re.compile(pattern), kind, target, priority, value))

    def scan(self, text):
        found = []
        for order, (rx, kind, target, priority, value) in enumerate(self.patterns):
            for m in rx.finditer(text):
                found.append(((-(m.end() - m.start()), -priority, m.start(), order),
                              Mention(kind, target, m.start(), m.end(), m.group(0), value)))
        chosen = []
        for _, men in sorted(found, key=lambda t: t[0]):   # longest, then most specific
            if all(men.end <= c.start or men.start >= c.end for c in chosen):
                chosen.append(men)
        return sorted(chosen, key=lambda m: m.start)


class Interpreter:
    def __init__(self, profile, semantic, choices=None):
        self.profile = profile
        self.semantic = semantic
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
                             [(f"{c.column}  ({c.reason})", c.column) for c in tied], remember=True)
        options = [(c, c) for c in self._columns_of_kind(config.SEMANTIC_ROLES[role]["kind"])]
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
        options = [(n, n) for n in (names or self._columns_of_kind("numeric"))]
        message = (f"Se encontraron varias columnas posibles para '{word}'. Seleccione una." if names
                   else f"No se encontro una columna para '{word}'. Seleccione una.")
        return self._ask(key, message, options, remember=True)

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
        return "date" if col.is_date else ("text" if col.kind == "boolean" else col.kind)

    def _resolve_role_silently(self, role):
        """Column for a role when it is unambiguous or already chosen; None otherwise."""
        return self._choice(f"col:{role}") or self.semantic.resolved(role)

    # --- main ------------------------------------------------------------------

    def build(self):
        text = self.p.normalized
        if not text:
            raise _Need(unrecognized(["el texto de la pregunta"]))
        if self.p.kind == s.TRUE_FALSE and self.p.claim is None:
            raise _Need(unrecognized(["el valor afirmado (por ejemplo: '... es 15')"]))
        text = re.sub(lx.COUNT_ROWS_PHRASE, "cuantos registros", text)
        text, dates = self._extract_dates(text)
        self.mentions, masked = self._scan(text, self.it.index_op)
        if any(m.kind == "op_role" for m in self.mentions) and not any(
                re.search(rx, masked) for rx in lx.OPERATION_KEYWORDS):
            # 'precio maximo' with no other operation word: 'maximo' IS the operation.
            self.mentions, masked = self._scan(text, self.it.index)
        masked, date_filters = self._date_filters(masked, dates)
        masked, conditions = self._conditions(masked)
        self.masked = masked
        spec = self._shape(masked, conditions, date_filters)
        spec.question_type = self.p.kind
        spec.options = list(self.p.options)
        spec.claim = self.p.claim
        return spec

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
        pattern = re.compile(rf"@m(\d+)@{lx.COPULA}\s*(?P<op>{ops}){lx.ARTICLES}\s*"
                             rf"(?:@m(?P<right>\d+)@|(?P<num>-?\d[\d.,]*\d|-?\d))(?![\d@])")
        conditions = []

        def replace(m):
            left = self.mentions[int(m.group(1))]
            if self._kind(left) not in ("numeric", "any") or left.used:
                return m.group(0)
            op = next(sql for rx, sql in lx.OPERATORS if re.fullmatch(rx, m.group("op")))
            if m.group("right") is not None:
                right = self.mentions[int(m.group("right"))]
                if self._kind(right) != "numeric":
                    return m.group(0)
                cond = Condition(self._resolve(left), op, other_column=self._resolve(right))
                right.used = True
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

        count_word = has(lx.COUNT) or has(lx.MORE_ROWS) or (
            self.p.kind == s.TRUE_FALSE and has(lx.ROWS) and not (has(lx.AVG) or has(lx.SUM) or has(lx.MAX) or has(lx.MIN)))
        maxw, minw = re.search(lx.MAX, masked), re.search(lx.MIN, masked)
        order = None
        if maxw or minw or has(lx.MORE_ROWS):
            order = "ASC" if minw and (not maxw or minw.start() < maxw.start()) else "DESC"
        pct_change, period = has(lx.PCT_CHANGE), has(lx.PERIOD)
        percent = has(lx.PERCENT) and not pct_change

        entity, entity_source, ask_date = self._entity(masked)
        if has(r"(?<![\w@])(?:cuando|when)(?![\w@])"):
            ask_date = True
        filters = conditions + self._value_filters()
        top_n = re.search(lx.TOP_N, masked)
        limit = int(top_n.group(1)) if top_n else None

        base = dict(filters=filters, date_filters=date_filters, order=order, limit=limit)

        # Counting ---------------------------------------------------------------
        if count_word and not pct_change and not percent:
            distinct_target = self._distinct_target(masked, entity, entity_source)
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
        if target is None and numbers:
            if len(numbers) > 1 and not (has(lx.COMPARE) or self._joined_by_or(masked)):
                names = list(dict.fromkeys(self._resolve(m) for m in numbers))
                if len(names) > 1:
                    chosen = self._ask("col:metric", "Se mencionan varias columnas numericas. Seleccione la que desea analizar.",
                                       [(n, n) for n in names])
                    target = Metric("column", (chosen,))
            if target is None and not (has(lx.COMPARE) or self._joined_by_or(masked)):
                target = Metric("column", (self._resolve(numbers[0]),))

        # Comparison of two columns -------------------------------------------------
        if (has(lx.COMPARE) or self._joined_by_or(masked)) and len(numbers) >= 2 and target is None:
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
                options = [(c, c) for c in self._columns_of_kind("numeric")]
                chosen = self._ask("col:metric", "No se identifico la columna numerica. Seleccione una.", options)
                target = Metric("column", (chosen,))
            else:
                raise _Need(unrecognized(["la operacion (promedio, suma, maximo, minimo o conteo)",
                                          "la columna a analizar (por ejemplo: " +
                                          ", ".join(self._columns_of_kind("numeric")[:4]) + ")"]))

        intent = {"difference": s.DIFFERENCE, "pct_change": s.PCT_CHANGE}.get(target.kind)
        agg = self._aggregation(masked)
        record = has(lx.RECORD)

        # A group (company, product...) is involved ----------------------------------
        if entity:
            explicit_agg = agg in ("AVG", "SUM")
            if record and not explicit_agg and order:
                return QuerySpec(intent or s.RECORD_EXTREME, s.RECORD, None, target, answer=f"column:{entity}", **base)
            if not explicit_agg:
                if order:
                    options = [(AGG_LABELS["SUM"], "SUM"), (AGG_LABELS["AVG"], "AVG"),
                               ("Valor de un solo registro (registro extremo)", "RECORD")]
                else:
                    options = [(AGG_LABELS[k], k) for k in ("SUM", "AVG", "MAX", "MIN")]
                agg = self._ask("agg", f"Que calculo desea por {entity}?", options)
                if agg == "RECORD":
                    return QuerySpec(intent or s.RECORD_EXTREME, s.RECORD, None, target, answer=f"column:{entity}", **base)
            shape = s.TOP if order else s.GROUP
            return QuerySpec(intent or (s.GROUP_TOP if shape == s.TOP else s.GROUP_AGG), shape, agg, target,
                             group_by=entity, answer="label" if entity_source != "group" else "value", **base)

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
        return QuerySpec(intent or default_intent, s.SCALAR, agg, target, **{**base, "order": None})

    # --- pieces ------------------------------------------------------------------

    def _entity(self, masked):
        """Grouping column: 'por empresa', 'que empresa', 'la empresa con ...'."""
        ask_date = False
        for rx, source in ((lx.GROUP_BY, "group"), (lx.WHICH, "which"), (lx.ENTITY_WITH, "with")):
            for m in re.finditer(rx, masked):
                men = self.mentions[int(m.group(1))]
                kind = self._kind(men)
                if men.used or kind in ("numeric", "value"):
                    continue
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
                m.used = True
                by_column.setdefault(m.target, [])
                if m.value not in by_column[m.target]:
                    by_column[m.target].append(m.value)
        return [Condition(col, "=", value=vals[0]) if len(vals) == 1 else Condition(col, "IN", value=vals)
                for col, vals in by_column.items()]

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
        found = [(m.start(), agg) for rx, agg in ((lx.AVG, "AVG"), (lx.SUM, "SUM"))
                 for m in re.finditer(rx, masked)]
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
