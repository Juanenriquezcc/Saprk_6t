"""Quality rules of the lab, typed by the student or read from a reglas.txt file.

One rule per line. A rule is a Spark SQL boolean expression over the columns:

    quantity > 0 AND quantity <= 20
    customer_age BETWEEN 18 AND 100
    returned_qty <= quantity
    year(order_date) = 2025
    status IN ('Entregado', 'Devuelto', 'Cancelado')

Shortcuts (Spanish):
    quantity entre 1 y 20             -> quantity BETWEEN 1 AND 20
    requerido: city, product          -> city IS NOT NULL / product IS NOT NULL
    año(order_date) = 2025            -> year(order_date) = 2025
    derivada: ingresos = quantity * unit_price    -> new column (not a check)
    # comment

Nothing is hardcoded for a dataset: the rules come from the lab statement.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import config
from app.errors import AppError

CHECK = "check"
DERIVED = "derived"

_BETWEEN = re.compile(r"^\s*(`[^`]+`|[\w.]+)\s+entre\s+(\S+)\s+y\s+(\S+)\s*$", re.IGNORECASE)
_REQUIRED = re.compile(r"^\s*(?:requerid[oa]s?|obligatori[oa]s?|no\s+nul[oa]s?|required)\s*:\s*(.+)$", re.IGNORECASE)
_DERIVED = re.compile(r"^\s*(?:derivada|columna|derived)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", re.IGNORECASE)
_YEAR = re.compile(r"\ba(?:ñ|n|ni)o\s*\(", re.IGNORECASE)     # año( / ano( / anio(
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Rule:
    kind: str          # check | derived
    text: str          # as written by the student
    expr: str          # Spark SQL expression
    name: str = None   # derived column name

    def to_dict(self):
        return {"tipo": "regla" if self.kind == CHECK else "columna derivada", "texto": self.text,
                "expresion": self.expr, **({"columna": self.name} if self.name else {})}


def parse_rules(text):
    """Text with one rule per line -> [Rule]. Raises AppError on a malformed line."""
    rules = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        line = line.rstrip(";").strip()
        line = _YEAR.sub("year(", line)
        m = _DERIVED.match(line)
        if m:
            rules.append(Rule(DERIVED, raw.strip(), m.group(2).strip(), m.group(1)))
            continue
        m = _REQUIRED.match(line)
        if m:
            for col in (c.strip() for c in m.group(1).split(",")):
                if not col:
                    continue
                rules.append(Rule(CHECK, f"{col} es requerido", f"{_ident(col)} IS NOT NULL"))
            continue
        m = _BETWEEN.match(line)
        if m:
            rules.append(Rule(CHECK, raw.strip(), f"{m.group(1)} BETWEEN {m.group(2)} AND {m.group(3)}"))
            continue
        rules.append(Rule(CHECK, raw.strip(), line))
    return rules


def find_rules_file(dataset_path):
    """reglas.txt (or <name>.reglas.txt) next to the dataset, or None."""
    folder = dataset_path if dataset_path.is_dir() else dataset_path.parent
    stem = dataset_path.stem
    for pattern in config.RULES_FILE_NAMES:
        candidate = folder / pattern.format(stem=stem)
        if candidate.is_file():
            return candidate
    return None


def read_rules_file(path):
    try:
        return parse_rules(Path(path).read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        return parse_rules(Path(path).read_text(encoding="latin-1"))


def check_rule(df, rule):
    """Raises AppError when the expression is not valid SQL over `df` (no Spark job runs)."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import BooleanType

    if rule.kind == DERIVED and not _NAME.match(rule.name):
        raise AppError(f"Nombre de columna derivada no valido: {rule.name}")
    try:
        data_type = df.select(F.expr(rule.expr).alias("r")).schema["r"].dataType
    except Exception as exc:
        from app.query.executor import translate_error
        error = translate_error(exc, df.columns)
        raise AppError(f"La regla '{rule.text}' no es valida: {error.message}",
                       error.hint or "Escriba la regla como una condicion SQL, por ejemplo: quantity > 0") from exc
    if rule.kind == CHECK and not isinstance(data_type, BooleanType):
        raise AppError(f"La regla '{rule.text}' no es una condicion (verdadero/falso).",
                       "Use comparaciones: >, <, =, BETWEEN, IN, IS NOT NULL.")


def _ident(name):
    return name if _NAME.match(name) else "`" + name.strip("`").replace("`", "``") + "`"
