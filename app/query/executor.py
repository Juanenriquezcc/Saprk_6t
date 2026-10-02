"""Runs Spark SQL and returns a small, bounded result."""
import difflib
import logging
import re
import time
from dataclasses import dataclass, field

import config
from app.errors import AppError, short_error

log = logging.getLogger(__name__)

# Manual SQL is read-only: the dataset view and the session must never be modified.
_READ_ONLY_START = re.compile(r"^(?:select|with|show|describe|desc|explain|values|table)\b", re.IGNORECASE)
_WRITE_WORDS = re.compile(r"\b(?:insert|create|drop|alter|delete|update|merge|truncate|cache|uncache|"
                          r"refresh|msck|grant|revoke)\b", re.IGNORECASE)
_WRITE_START = re.compile(r"^(?:set|reset|use|add|load|clear|analyze|repair|declare)\b", re.IGNORECASE)


@dataclass
class QueryResult:
    sql: str
    columns: list
    rows: list          # list of tuples (bounded by `max_rows`)
    truncated: bool
    seconds: float
    types: list = field(default_factory=list)   # Spark type of each result column

    def first(self, column):
        return self.rows[0][self.columns.index(column)] if self.rows else None

    def as_dicts(self):
        return [dict(zip(self.columns, r)) for r in self.rows]


def execute(spark, sql, max_rows=None, known_columns=()):
    """spark.sql(sql) bringing at most `max_rows` rows to Python (never the whole dataset)."""
    cap = max_rows or config.MAX_GROUP_ROWS
    start = time.perf_counter()
    for attempt in (1, 2):
        try:
            df = spark.sql(sql)
            rows = [tuple(r) for r in df.limit(cap + 1).collect()]
            break
        except Exception as exc:
            # Rarely PySpark hands back a raw Java error instead of converting it (seen twice
            # in hundreds of test runs). Every query here is read-only, so one retry is safe.
            if attempt == 1 and type(exc).__name__ == "Py4JJavaError":
                log.debug("Error de Java sin convertir; se reintenta una vez: %s", short_error(exc))
                continue
            raise translate_error(exc, known_columns) from exc
    return QueryResult(sql=sql, columns=list(df.columns), rows=rows[:cap], truncated=len(rows) > cap,
                       seconds=time.perf_counter() - start, types=[t for _, t in df.dtypes])


def check_read_only(sql):
    """Returns the cleaned statement or raises AppError (one read-only statement only)."""
    statement = re.sub(r"--[^\n]*", " ", sql).strip().rstrip(";").strip()
    if not statement:
        raise AppError("La consulta esta vacia.")
    without_literals = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`", "''", statement)
    if ";" in without_literals:
        raise AppError("Escriba una sola consulta a la vez.", "Separe las consultas y ejecutelas una por una.")
    if _WRITE_START.match(statement) or _WRITE_WORDS.search(without_literals):
        raise AppError("Solo se permiten consultas de lectura (SELECT, WITH, SHOW, DESCRIBE, EXPLAIN).",
                       f"La vista '{config.VIEW_NAME}' y la sesion no se pueden modificar desde aqui.")
    if not _READ_ONLY_START.match(statement):
        word = statement.split()[0]
        raise AppError(f"La consulta debe empezar con SELECT, WITH, SHOW, DESCRIBE o EXPLAIN (empieza con '{word}').",
                       "Revise si hay un error de escritura.")
    return statement


def _error_text(exc):
    """Text of the whole exception chain (an unconverted Py4JJavaError keeps the Spark
    error class only in the Java message)."""
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        try:
            parts.append(str(exc))
        except Exception:   # str() of a Java error may need the gateway
            pass
        exc = exc.__cause__ or exc.__context__
    return "\n".join(parts)


def _first_line(text):
    for line in text.splitlines():
        line = line.strip().lstrip(": ").strip()
        if line and not line.startswith("An error occurred while calling"):
            return re.sub(r"^[\w.$]+(?:Exception|Error): ", "", line)[:300]
    return "error desconocido"


def translate_error(exc, known_columns=()):
    text = _error_text(exc)
    first = _first_line(text) if "An error occurred while calling" in short_error(exc) else short_error(exc)
    if "UNRESOLVED_COLUMN" in text or "cannot be resolved" in text:
        missing = re.search(r"`([^`]+)`", first)
        name = missing.group(1) if missing else "?"
        close = difflib.get_close_matches(name, list(known_columns), n=3)
        hint = f"Quiso decir: {', '.join(close)}" if close else "Revise los nombres en 'Ver esquema'."
        return AppError(f"La columna '{name}' no existe en el dataset.", hint)
    if "TABLE_OR_VIEW_NOT_FOUND" in text:
        return AppError("La tabla o vista no existe: " + first, f"Use la vista '{config.VIEW_NAME}'.")
    if "PARSE_SYNTAX_ERROR" in text or "ParseException" in type(exc).__name__:
        return AppError("La consulta SQL tiene un error de sintaxis: " + first, "Revise la consulta.")
    if "MISSING_AGGREGATION" in text or "MISSING_GROUP_BY" in text:
        return AppError("Hay columnas que no estan en GROUP BY ni dentro de una agregacion.",
                        "Agregue la columna al GROUP BY o use AVG/SUM/MIN/MAX/COUNT.")
    if "DIVIDE_BY_ZERO" in text:
        return AppError("Division por cero en la consulta.", "Use NULLIF(divisor, 0) en el divisor.")
    if "CAST_INVALID_INPUT" in text:
        return AppError("Hay valores que no se pueden convertir al tipo pedido.", "Use try_cast(...) para ignorarlos.")
    if "DATATYPE_MISMATCH" in text:
        return AppError("Tipos de datos incompatibles en la consulta: " + first,
                        "Revise que las columnas numericas no se mezclen con texto.")
    return AppError("Spark no pudo ejecutar la consulta: " + first)
