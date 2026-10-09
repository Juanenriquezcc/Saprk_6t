"""TRANSFORM: finds what needs cleaning in the text columns and applies it with Spark.

Detection is one aggregation over every text column (plus one small GROUP BY per
candidate category column). Each change is a Transformation with a description and
the number of affected values, so the ETL report can list it.

Rules (nothing is invented, nothing is filled in):
  - trim: leading/trailing spaces removed.
  - empty values: '', 'N/A', 'null', '-' ... become NULL (config.NULL_TOKENS).
  - numbers written as text ('1.234,5', '$ 12,000') become numbers when at least
    config.ETL_CONVERT_MIN_RATIO of the values have that shape. The decimal separator is
    decided by the data; when the data cannot decide, the user is asked.
  - dates written as text become dates (same ratio). Day/month order is decided by the
    data (a part > 12); when it cannot be decided, the user is asked.
  - categories that differ only in case, accents or spaces ('bogota', 'BOGOTÁ ') are
    unified to their most frequent spelling.
Values that do not fit the new type are kept apart (column __raw__<name>) so the
validator can reject the row and report it; they are never replaced by a guess.
"""
import re
from dataclasses import dataclass, field

import config
from app.text import strip_accents

RAW_PREFIX = "__raw__"

# Shapes of numbers written as text (after removing currency symbols and spaces).
_INT = r"^-?\d+$"
_DOT_DEC = r"^-?\d+\.\d+$"            # 12.5  (or 1.234 = thousands in Spanish notation)
_COMMA_DEC = r"^-?\d+,\d+$"           # 12,5  (or 1,234 = thousands in English notation)
_DOT_DEC3 = r"^-?\d{1,3}\.\d{3}$"     # the ambiguous ones: exactly three digits after the mark
_COMMA_DEC3 = r"^-?\d{1,3},\d{3}$"
_EU = r"^-?\d{1,3}(\.\d{3})+,\d+$|^-?\d{1,3}(\.\d{3}){2,}$"    # 1.234,5   1.234.567
_US = r"^-?\d{1,3}(,\d{3})+\.\d+$|^-?\d{1,3}(,\d{3}){2,}$"     # 1,234.5   1,234,567
# Dates written as text (time part ignored).
_ISO = r"^\d{4}-\d{1,2}-\d{1,2}$"
_ISO_SLASH = r"^\d{4}/\d{1,2}/\d{1,2}$"
_DMY = r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$"
_DAY_FIRST = r"^(1[3-9]|2\d|3[01])[/-]\d{1,2}[/-]\d{4}$"       # first part > 12 -> day first
_MONTH_FIRST = r"^\d{1,2}[/-](1[3-9]|2\d|3[01])[/-]\d{4}$"     # second part > 12 -> month first


@dataclass
class Transformation:
    kind: str                     # trim | nulls | number | date | category
    column: str
    description: str
    affected: int = 0             # values changed (or values that will be converted)
    details: list = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def to_dict(self):
        data = {"tipo": self.kind, "columna": self.column, "descripcion": self.description,
                "valores_afectados": self.affected, "detalle": self.details}
        if self.kind == "category":   # traceability: every original spelling -> value kept
            data["regla"] = "misma palabra salvo mayusculas, tildes o espacios -> escritura mas frecuente"
            data["mapeo"] = dict(sorted(self.params["mapping"].items()))
        if self.kind == "equivalence":
            data["regla"] = "equivalencia de la configuracion del caso, confirmada por el usuario"
            data["de"], data["a"] = self.params["from"], self.params["to"]
        return data


@dataclass
class Decision:
    """Something only the user can decide (asked before applying)."""
    key: str
    message: str
    options: list                 # [(label, value)]
    details: list = field(default_factory=list)   # lines shown before the options


def q(name):
    return "`" + name.replace("`", "``") + "`"


def clean_text(col):
    """Trimmed text, NULL for the empty tokens (Spark expression)."""
    from pyspark.sql import functions as F
    trimmed = F.trim(F.col(q(col)))
    tokens = [t for t in config.NULL_TOKENS]
    return F.when(F.lower(trimmed).isin(tokens), F.lit(None)).otherwise(trimmed)


def _number_text(col):
    from pyspark.sql import functions as F
    # Currency marks and inner spaces: '$ 12.000', 'COP 5,000', '12 %'
    return F.regexp_replace(clean_text(col), r"(?i)\s|\$|€|cop|usd|eur|%", "")


def _date_text(col):
    from pyspark.sql import functions as F
    return F.split(clean_text(col), r"[ T]").getItem(0)


def text_key(col):
    """Spark version of category_key(): lowercase, no accents, single inner spaces, trimmed."""
    from pyspark.sql import functions as F
    from app.schema.values import ACCENTS, PLAIN
    return F.translate(F.lower(F.regexp_replace(F.trim(F.col(q(col))), r"\s+", " ")), ACCENTS, PLAIN)


def detect(df, load=None):
    """Returns ([Transformation], [Decision], [similar values]). Decisions must be answered before
    apply(). Similar values ('Laptop Pro14' / 'Laptop Pro 14') are only reported, never merged."""
    from pyspark.sql import functions as F

    transformations, decisions = _thousands_read_as_decimals(df, load)
    suggestions = []
    text_cols = [n for n, t in df.dtypes if t == "string" and not n.startswith(RAW_PREFIX)]
    if not text_cols:
        return transformations, decisions, suggestions

    exprs = []
    for i, c in enumerate(text_cols):
        raw, clean, num, date = F.col(q(c)), clean_text(c), _number_text(c), _date_text(c)
        exprs += [
            F.count_if(raw.isNotNull() & (raw != F.trim(raw))).alias(f"trim_{i}"),
            F.count_if(raw.isNotNull() & clean.isNull()).alias(f"empty_{i}"),
            F.count_if(clean.isNotNull()).alias(f"filled_{i}"),
            F.approx_count_distinct(raw).alias(f"distinct_{i}"),
        ]
        for name, rx in (("int", _INT), ("dotdec", _DOT_DEC), ("commadec", _COMMA_DEC), ("dot3", _DOT_DEC3),
                         ("comma3", _COMMA_DEC3), ("eu", _EU), ("us", _US)):
            exprs.append(F.count_if(num.rlike(rx)).alias(f"{name}_{i}"))
        for name, rx in (("iso", _ISO), ("isoslash", _ISO_SLASH), ("dmy", _DMY), ("dayfirst", _DAY_FIRST),
                         ("monthfirst", _MONTH_FIRST)):
            exprs.append(F.count_if(date.rlike(rx)).alias(f"{name}_{i}"))
    stats = df.agg(*exprs).first().asDict()

    trims ={c: stats[f"trim_{i}"] for i, c in enumerate(text_cols) if stats[f"trim_{i}"]}
    if trims:
        transformations.append(Transformation(
            "trim", ", ".join(trims), "Espacios al inicio/final eliminados", sum(trims.values()),
            [f"{c}: {n}" for c, n in trims.items()], {"columns": list(trims)}))
    empties = {c: stats[f"empty_{i}"] for i, c in enumerate(text_cols) if stats[f"empty_{i}"]}
    if empties:
        transformations.append(Transformation(
            "nulls", ", ".join(empties), "Valores vacios ('', N/A, null, -) convertidos a nulo", sum(empties.values()),
            [f"{c}: {n}" for c, n in empties.items()], {"columns": list(empties)}))

    categorical = []
    for i, c in enumerate(text_cols):
        s = {k.rsplit("_", 1)[0]: v for k, v in stats.items() if k.endswith(f"_{i}")}
        filled = s["filled"]
        if not filled:
            continue
        number = _number_plan(c, s, filled)
        if number:
            transformations.append(number[0])
            if number[1]:
                decisions.append(number[1])
            continue
        date = _date_plan(c, s, filled)
        if date:
            transformations.append(date[0])
            if date[1]:
                decisions.append(date[1])
            continue
        if s["distinct"] <= config.ETL_CATEGORY_MAX_DISTINCT:
            categorical.append(c)

    for c in categorical:
        t, similar = _category_plan(df, c)
        if t:
            transformations.append(t)
        suggestions += similar
    return transformations, decisions, suggestions


def _number_plan(col, s, filled):
    numeric = s["int"] + s["dotdec"] + s["commadec"] + s["eu"] + s["us"]
    if numeric < config.ETL_CONVERT_MIN_RATIO * filled:
        return None
    eu_evidence = s["eu"] + (s["commadec"] - s["comma3"])     # 1.234,5 or 12,5
    us_evidence = s["us"] + (s["dotdec"] - s["dot3"])         # 1,234.5 or 12.5
    ambiguous3 = s["dot3"] + s["comma3"]
    invalid = filled - numeric
    t = Transformation("number", col, f"{col}: texto convertido a numero", numeric,
                       params={"invalid": invalid})
    decision = None
    if eu_evidence and us_evidence:
        decision = Decision(f"number:{col}",
                            f"La columna '{col}' mezcla numeros escritos como 1.234,5 y como 1,234.5. "
                            "Seleccione el separador decimal correcto.",
                            [("Coma decimal (1.234,5)", "eu"), ("Punto decimal (1,234.5)", "us")])
    elif eu_evidence:
        t.params["format"] = "eu"
    elif us_evidence:
        t.params["format"] = "us"
    elif ambiguous3:
        t.params["sep3"] = "." if s["dot3"] >= s["comma3"] else ","
        decision = Decision(f"number:{col}",
                            f"En la columna '{col}' hay valores como '1.234' o '1,234' que no permiten saber "
                            "el separador decimal. Seleccione como leerlos.",
                            [("Punto y coma son miles: 1.234 = mil doscientos treinta y cuatro", "thousands"),
                             ("Son decimales: 1.234 = uno coma dos tres cuatro", "decimal")])
    else:
        t.params["format"] = "int" if s["int"] == numeric else "us"
    if invalid:
        t.details.append(f"{invalid} valor(es) no numericos: se rechazan en la validacion")
    return t, decision


def _date_plan(col, s, filled):
    dates = s["iso"] + s["isoslash"] + s["dmy"]
    if dates < config.ETL_CONVERT_MIN_RATIO * filled:
        return None
    invalid = filled - dates
    t = Transformation("date", col, f"{col}: texto convertido a fecha", dates, params={"invalid": invalid})
    decision = None
    if not s["dmy"]:
        t.params["order"] = "iso"
    else:
        if s["dayfirst"] and s["monthfirst"]:
            t.details.append(f"mezcla de formatos: {s['dayfirst']} con dia primero y {s['monthfirst']} con mes primero")
            decision = Decision(f"date:{col}",
                                f"La columna '{col}' tiene fechas con el dia primero y otras con el mes primero. "
                                "Seleccione el formato de las fechas ambiguas (ej. 03/04/2025).",
                                [("dd/mm/aaaa (dia primero)", "dmy"), ("mm/dd/aaaa (mes primero)", "mdy")])
        elif s["dayfirst"]:
            t.params["order"] = "dmy"
        elif s["monthfirst"]:
            t.params["order"] = "mdy"
        else:
            decision = Decision(f"date:{col}",
                                f"Las fechas de '{col}' (ej. 03/04/2025) no permiten saber si el dia va primero. "
                                "Seleccione el formato.",
                                [("dd/mm/aaaa (dia primero)", "dmy"), ("mm/dd/aaaa (mes primero)", "mdy")])
    if invalid:
        t.details.append(f"{invalid} valor(es) que no son fechas: se rechazan en la validacion")
    return t, decision


def _thousands_read_as_decimals(df, load):
    """CSV numbers like '12.500' (Spanish thousands) are read by Spark as 12.5 without any
    error. When every decimal number in the first KB of a numeric column has exactly three
    digits after the dot, the text is ambiguous and the user decides."""
    if load is None or load.fmt != "csv" or not load.csv_options:
        return [], []
    import csv as _csv
    from app.spark.loader import sample_text

    opts = load.csv_options
    lines = list(_csv.reader(sample_text(load.path).splitlines(), delimiter=opts.sep))[1 if opts.header else 0:]
    names = [n for n in load.columns]
    transformations, decisions = [], []
    for i, (name, dtype) in enumerate(df.dtypes):
        if dtype not in ("double", "float") or i >= len(names) or names[i] != name:
            continue
        tokens = [r[i].strip() for r in lines if len(r) > i and "." in r[i]]
        if tokens and all(re.fullmatch(r"-?[1-9]\d{0,2}\.\d{3}", t) for t in tokens):
            transformations.append(Transformation(
                "scale", name, f"{name}: '1.234' leido como miles (x1000)", 0,
                [f"ejemplos en el archivo: {', '.join(tokens[:3])}"]))
            decisions.append(Decision(
                f"number:{name}",
                f"En la columna '{name}' los numeros se escriben como {tokens[0]}: Spark los leyo como decimales. "
                "Que significan?",
                [(f"Miles: {tokens[0]} = {tokens[0].replace('.', '')}", "thousands"),
                 (f"Decimales: {tokens[0]} = {tokens[0]}", "decimal")]))
    return transformations, decisions


def category_key(value):
    return " ".join(strip_accents(str(value)).lower().split())


def _category_plan(df, col):
    """Spellings that differ only in case/accents/spaces -> the most frequent one. Also returns the
    values that differ only in inner spaces or punctuation ('Pro14' / 'Pro 14'): they may be
    different things, so they are reported for an explicit equivalence and never merged here."""
    from pyspark.sql import functions as F

    cap = config.ETL_CATEGORY_MAX_DISTINCT
    rows = (df.select(clean_text(col).alias("v")).where(F.col("v").isNotNull())
            .groupBy("v").count().limit(cap + 1).collect())
    if len(rows) > cap:     # the approximate count was low: too many values to be a category
        return None, []
    groups = {}
    for r in rows:
        groups.setdefault(category_key(r["v"]), []).append((r["v"], r["count"]))
    mapping, details, affected, compact = {}, [], 0, {}
    for variants in groups.values():
        # Most frequent; on a tie, the spelling with capitals/accents (likely the edited one), then A-Z.
        canonical = sorted(variants, key=lambda v: (-v[1], v[0] == v[0].lower() or v[0] == v[0].upper(), v[0]))[0][0]
        compact.setdefault(re.sub(r"[\W_]+", "", category_key(canonical)), []).append(
            (canonical, sum(n for _, n in variants)))
        if len(variants) < 2:
            continue
        for value, count in variants:
            if value != canonical:
                mapping[value] = canonical
                affected += count
        details.append(f"{' / '.join(repr(v) for v, _ in sorted(variants))} -> {canonical!r}")
    similar = [f"{col}: " + " / ".join(f"{v!r} ({n})" for v, n in sorted(values))
               for values in compact.values() if len(values) > 1]
    if not mapping:
        return None, similar
    return Transformation("category", col, f"{col}: {len(details)} categoria(s) con escrituras distintas unificadas",
                          affected, sorted(details), {"mapping": mapping}), similar


def apply(df, transformations, answers=None):
    """Applies the chosen transformations (lazy). `answers` = {decision key: value}."""
    from pyspark.sql import functions as F

    answers = answers or {}
    trim_cols, null_cols = set(), set()
    by_column = {}
    for t in transformations:
        if t.kind == "trim":
            trim_cols.update(t.params["columns"])
        elif t.kind == "nulls":
            null_cols.update(t.params["columns"])
        else:
            by_column[t.column] = t

    cols = []
    for name in df.columns:
        col = F.col(q(name))
        t = by_column.get(name)
        if t is None:
            if name in null_cols:
                col = clean_text(name) if name in trim_cols else \
                    F.when(F.lower(F.trim(col)).isin(list(config.NULL_TOKENS)), F.lit(None)).otherwise(col)
            elif name in trim_cols:
                col = F.trim(col)
            cols.append(col.alias(name))
            continue
        if t.kind == "scale":
            # round(): 12.5 * 1000 must give exactly 12500, not 12499.999...
            cols.append(F.round(F.col(q(name)) * 1000, 6).alias(name))
            continue
        if t.kind == "category":
            clean = clean_text(name)
            mapping = F.create_map(*[F.lit(x) for kv in t.params["mapping"].items() for x in kv])
            cols.append(F.coalesce(mapping[clean], clean).alias(name))
            continue
        cols.append(F.col(q(name)).alias(RAW_PREFIX + name))   # kept to detect invalid values
        if t.kind == "number":
            cols.append(_to_number(name, _number_format(t, answers.get(f"number:{name}"))).alias(name))
        else:
            order = t.params.get("order") or answers.get(f"date:{name}")
            if order is None:
                raise ValueError(f"Falta la decision del formato de fecha de {name}")
            cols.append(_to_date(name, order).alias(name))
    return df.select(*cols)


def _number_format(t, answer):
    if t.params.get("format"):
        return t.params["format"]
    if answer in ("thousands", "decimal"):
        dot = t.params.get("sep3") == "."
        # 'thousands': '1.234' drops the dot (eu) / '1,234' drops the comma (us).
        return ("eu" if dot else "us") if answer == "thousands" else ("us" if dot else "eu")
    if answer not in ("eu", "us"):
        raise ValueError(f"Falta la decision del formato numerico de {t.column}")
    return answer


def _to_number(name, fmt):
    from pyspark.sql import functions as F
    text = _number_text(name)
    if fmt == "eu":
        text = F.regexp_replace(F.regexp_replace(text, r"\.", ""), ",", ".")
    else:
        text = F.regexp_replace(text, ",", "")
    # try_cast: an invalid value becomes NULL instead of failing (ANSI mode is on).
    return text.try_cast("bigint" if fmt == "int" else "double")


def _to_date(name, order):
    from pyspark.sql import functions as F
    text = F.regexp_replace(_date_text(name), "-", "/")
    # The chosen order first; a date it cannot read (25/03 read month-first) tries the other one,
    # so only the truly ambiguous dates (03/04) depend on the choice.
    patterns = ["yyyy/M/d"] + {"dmy": ["d/M/yyyy", "M/d/yyyy"], "mdy": ["M/d/yyyy", "d/M/yyyy"]}.get(order, [])
    return F.coalesce(*[F.call_function("try_to_date", text, F.lit(p)) for p in patterns])
