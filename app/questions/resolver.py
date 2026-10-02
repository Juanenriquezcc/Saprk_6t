"""QuerySpec -> SQL -> result -> answer + evidence (open, multiple choice, true/false)."""
import datetime
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from app.query import spec as s
from app.query.builder import build_sql, build_tie_check
import config
from app.query.executor import QueryResult, execute
from app.questions.intents import NeedsInput
from app.questions.numbers import format_number, matches, read_number
from app.text import normalize

NO_MATCH = "Ninguna opcion coincide con el resultado calculado."
AGG_TEXT = {"SUM": "suma", "AVG": "promedio", "MAX": "maximo", "MIN": "minimo", "COUNT": "conteo de registros",
            "COUNT_DISTINCT": "conteo de valores distintos", "PERCENT": "porcentaje", "PERIOD_CHANGE": "variacion % del periodo"}
AGG_WITH_ARTICLE = {"SUM": "la suma", "AVG": "el promedio", "MAX": "el maximo", "MIN": "el minimo"}


@dataclass
class Evidence:
    question: str
    question_type: str
    intent: str
    columns_used: list
    filters: list
    sql: str
    result_columns: list
    result_rows: list
    value: object                 # main computed value (or label)
    answer: str                   # final answer shown to the user
    result_text: str              # readable result (record, group and value), before options/claims
    interpretation: str
    timestamp: str
    seconds: float
    warnings: list = field(default_factory=list)
    extra_sql: list = field(default_factory=list)
    number: int = None
    options: list = field(default_factory=list)
    matched_option: str = None
    claim: str = None
    verdict: str = None           # VERDADERO | FALSO
    correct_value: object = None
    spec: dict = None
    result_types: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def evidence_for_sql(label, result, warnings=()):
    """Evidence of a statement typed by the user (MANUAL_SQL): there is no QuerySpec."""
    shown = len(result.rows)
    if shown == 1 and len(result.columns) == 1:
        text = f"{result.columns[0]} = {_text(result.rows[0][0])}"
    else:
        text = f"{shown} fila(s) mostrada(s)" + (" (hay mas filas)" if result.truncated else "")
    notes = list(warnings)
    if result.truncated:
        notes.append(f"Se muestran solo las primeras {shown} filas. Agregue LIMIT o filtros para acotar.")
    return Evidence(
        question=label, question_type=s.OPEN, intent=s.MANUAL_SQL, columns_used=[], filters=[], sql=result.sql,
        result_columns=result.columns, result_rows=[[_plain(v) for v in row] for row in result.rows],
        value=_plain(result.rows[0][0]) if shown == 1 and len(result.columns) == 1 else None,
        answer=text, result_text=text, interpretation="MANUAL_SQL: consulta escrita por el usuario (solo lectura).",
        timestamp=datetime.datetime.now().isoformat(timespec="seconds"), seconds=round(result.seconds, 3),
        warnings=notes, result_types=list(result.types))


def solve(spark, interpreter, parsed, extra_choices=None, number=None):
    """Evidence, or NeedsInput when the user must decide something first."""
    result = interpreter.interpret(parsed, extra_choices)
    if isinstance(result, NeedsInput):
        return result
    return run_spec(spark, parsed, result, [c.name for c in interpreter.profile.columns], number)


def run_spec(spark, parsed, spec, known_columns=(), number=None):
    built = build_sql(spec)
    result = execute(spark, built.sql, known_columns=known_columns)
    if spec.aggregation == "SUMMARY":
        result = _summary_table(spec, result)
    warnings, extra_sql = [], []
    if result.truncated:
        warnings.append(f"Se muestran solo las primeras {len(result.rows)} filas del resultado.")

    primary, alternates, answer_text = _extract(spec, built, result, warnings)

    if spec.shape in (s.TOP, s.RECORD) and (spec.limit or 1) == 1 and result.rows:
        best = result.first(built.value_column)
        if best is not None:
            tie_sql = build_tie_check(spec, built, best)
            extra_sql.append(tie_sql)
            ties = execute(spark, tie_sql, 1).rows[0][0]
            if ties > 1:
                what = "grupos" if spec.shape == s.TOP else "registros"
                warnings.append(f"Empate: {ties} {what} tienen el mismo valor ({format_number(best)}). "
                                f"Se muestra el primero en el orden de la consulta.")

    evidence = Evidence(
        question=parsed.raw.strip(), question_type=spec.question_type, intent=spec.intent,
        columns_used=spec.columns_used(), filters=spec.filter_labels(), sql=built.sql,
        result_columns=result.columns, result_rows=[[_plain(v) for v in row] for row in result.rows],
        value=_plain(primary), answer=answer_text, result_text=answer_text, interpretation=explain(spec),
        timestamp=datetime.datetime.now().isoformat(timespec="seconds"), seconds=round(result.seconds, 3),
        warnings=warnings, extra_sql=extra_sql, number=number if number is not None else parsed.number,
        options=[list(o) for o in spec.options], claim=spec.claim, spec=_plain_spec(spec),
        result_types=list(result.types))

    if spec.question_type == s.MULTIPLE_CHOICE:
        if spec.shape == s.RECORD and spec.answer == "row" and result.rows:
            letter, note = match_record_options(spec.options, result.as_dicts()[0])
        else:
            letter, note = match_options(spec.options, primary, alternates)
        evidence.matched_option = letter
        evidence.answer = f"{letter}) {dict(spec.options)[letter]}" if letter else NO_MATCH
        if note:
            evidence.warnings.append(note)
    elif spec.question_type == s.TRUE_FALSE:
        ok = any(_value_matches(spec.claim, v) for v in [primary, *alternates])
        evidence.verdict = "VERDADERO" if ok else "FALSO"
        evidence.correct_value = answer_text
        evidence.answer = evidence.verdict
    return evidence


# --- result -> answer ------------------------------------------------------------

def _extract(spec, built, result, warnings):
    """(primary value, alternative values, readable answer)."""
    if not result.rows:
        warnings.append("La consulta no devolvio filas (ningun registro cumple las condiciones).")
        return None, [], "Sin resultados"
    pct = "%" if spec.percentage else ""

    if spec.aggregation == "SUMMARY":
        return None, [], f"Resumen de {len(result.rows)} columna(s) (ver tabla)"
    if spec.shape == s.ROWS:
        return None, [], f"{len(result.rows)} registro(s) mostrado(s)"

    if spec.shape == s.SCALAR:
        value = result.first(built.value_column)
        if value is None:
            warnings.append("El resultado es NULL: no hay datos que cumplan las condiciones.")
        return value, [], format_number(value) + (pct if value is not None else "")

    if spec.shape == s.TOP:
        label, value = result.first(built.label_column), result.first(built.value_column)
        primary, other = (label, value) if spec.answer == "label" else (value, label)
        return primary, [other], f"{_text(label)} ({AGG_TEXT[spec.aggregation]} = {format_number(value)}{pct})"

    if spec.shape == s.GROUP:
        return None, [], f"{len(result.rows)} grupos (ver tabla de resultados)"

    if spec.shape == s.COMPARE:
        a_col, b_col = spec.comparison
        a, b = result.rows[0][0], result.rows[0][1]
        if a is None or b is None:
            return None, [], "Sin datos suficientes para comparar"
        if a == b:
            warnings.append(f"Empate: {a_col} y {b_col} tienen el mismo valor ({format_number(a)}).")
        winner = a_col if (a >= b if spec.order != "ASC" else a <= b) else b_col
        agg = AGG_TEXT[spec.aggregation]
        return winner, [a, b], f"{winner} ({agg} {a_col} = {format_number(a)}, {agg} {b_col} = {format_number(b)})"

    # RECORD
    row = result.as_dicts()[0]
    value = row.get(built.value_column)
    target = spec.target.label()
    if spec.answer.startswith("column:"):
        column = spec.answer.split(":", 1)[1]
        others = [value] + [v for k, v in row.items() if k != column]
        return row.get(column), others, f"{_text(row.get(column))} ({target} = {format_number(value)})"
    detail = ", ".join(f"{k}={_text(v)}" for k, v in row.items())
    return value, list(row.values()), f"{target} = {format_number(value)} | registro: {detail}"


def _summary_table(spec, result):
    """One wide row (stat__column ...) -> one row per column: [column, stat1, stat2, ...]."""
    if not result.rows:
        return result
    values = list(result.rows[0])
    n = len(spec.stats)
    rows = [tuple([col] + values[i * n:(i + 1) * n]) for i, col in enumerate(spec.columns)]
    return QueryResult(sql=result.sql, columns=["columna", *spec.stats], rows=rows, truncated=False,
                       seconds=result.seconds, types=[])


# --- multiple choice / true-false ------------------------------------------------

def match_options(options, primary, alternates):
    """(letter or None, note). Never forces an answer."""
    for candidates in ([primary], alternates):
        hits = [(letter, precision) for letter, text in options
                for precision in [_best_precision(text, candidates)] if precision is not None]
        if not hits:
            continue
        if len(hits) == 1:
            return hits[0][0], None
        best = max(p for _, p in hits)
        top = [letter for letter, p in hits if p == best]
        if len(top) == 1:
            return top[0], None
        return None, f"Varias opciones coinciden con el resultado ({', '.join(top)}); no se elige ninguna."
    return None, None


_DATE_TEXT = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}")


def match_record_options(options, row):
    """Options that describe a whole row ('NVDA - 2024-01-04'): each value of the row
    found in the option adds a point, each date or number that contradicts it subtracts one."""
    scores = {letter: _record_score(text, row) for letter, text in options}
    best = max(scores.values(), default=0)
    if best <= 0:
        return None, None
    top = [letter for letter, score in scores.items() if score == best]
    if len(top) == 1:
        return top[0], None
    return None, f"Varias opciones describen el registro ({', '.join(top)}); no se elige ninguna."


def _record_score(text, row):
    written = normalize(text)
    row_dates = {v.isoformat()[:10] for v in row.values() if isinstance(v, (datetime.date, datetime.datetime))}
    score = 0
    for d in _DATE_TEXT.findall(written):
        dmy = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
        y, mth, day = (dmy.group(3), dmy.group(2), dmy.group(1)) if dmy else d.split("-")
        score += 1 if f"{int(y):04d}-{int(mth):02d}-{int(day):02d}" in row_dates else -1
    rest = _DATE_TEXT.sub(" ", written)
    for value in row.values():
        if isinstance(value, str) and value.strip() and \
                re.search(rf"(?<!\w){re.escape(normalize(value))}(?!\w)", rest):
            score += 1
    numbers = [v for v in row.values() if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)]
    for token in re.findall(r"-?\d[\d.,]*\d|-?\d", rest):
        readings = read_number(token)
        score += 1 if any(matches(v, r) for v in numbers for r in readings) else -1
    return score


def _best_precision(text, candidates):
    best = None
    for value in candidates:
        p = _match_precision(text, value)
        if p is not None and (best is None or p > best):
            best = p
    return best


def _value_matches(text, value):
    return text is not None and _match_precision(text, value) is not None


def _match_precision(text, value):
    """How precisely the written `text` matches `value` (higher = better), or None."""
    if value is None or text is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        readings = read_number(text) or _embedded_number(text)
        good = [r.decimals for r in readings if matches(value, r)]
        return max(good) if good else None
    if isinstance(value, (datetime.date, datetime.datetime)):
        iso = value.isoformat()[:10]
        written = normalize(text)
        dmy = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", written)
        if dmy:
            written = f"{dmy.group(3)}-{int(dmy.group(2)):02d}-{int(dmy.group(1)):02d}"
        return 100 if written.startswith(iso) else None
    written, expected = normalize(text), normalize(str(value))
    if written == expected:
        return 100
    if re.search(rf"(?<!\w){re.escape(expected)}(?!\w)", written):
        return 50
    return None


def _embedded_number(text):
    nums = re.findall(r"[-+]?\$?\d[\d.,]*\d%?|[-+]?\d", text)
    return read_number(nums[0]) if len(nums) == 1 else []


# --- explanation -----------------------------------------------------------------

def explain(spec):
    m = spec.target.label() if spec.target else None
    agg = spec.aggregation
    if agg == "SUMMARY":
        what = f"se calcula {', '.join(spec.stats)} de: {', '.join(spec.columns)} (una sola consulta)"
    elif spec.shape == s.ROWS:
        what = f"se muestran los registros que cumplen las condiciones (maximo {spec.limit or config.MAX_DISPLAY_ROWS})"
    elif agg == "COUNT":
        what = "se cuentan los registros"
    elif agg == "COUNT_DISTINCT":
        what = f"se cuentan los valores distintos de {m}"
    elif agg == "PERCENT":
        cond = " y ".join(c.label() for c in spec.percent_filters)
        what = (f"se calcula que porcentaje de la suma de {m} cumple: {cond}" if m
                else f"se calcula el porcentaje de registros que cumplen: {cond}")
    elif agg == "PERIOD_CHANGE":
        what = f"se compara {m} en la primera y en la ultima fecha ({spec.period_column}), en porcentaje"
    elif spec.shape == s.COMPARE:
        what = f"se compara {AGG_WITH_ARTICLE[agg]} de {spec.comparison[0]} con {AGG_WITH_ARTICLE[agg]} de {spec.comparison[1]}"
    elif spec.shape == s.RECORD:
        what = f"se busca el registro con {'mayor' if spec.order != 'ASC' else 'menor'} {m}"
    else:
        what = f"se calcula {AGG_WITH_ARTICLE[agg]} de {m}"
    if spec.shape == s.GROUP:
        what += f" para cada {spec.group_by}"
    elif spec.shape == s.TOP:
        what += f" para cada {spec.group_by} y se toma el {'mayor' if spec.order != 'ASC' else 'menor'}"
        if spec.limit and spec.limit > 1:
            what += f" ({spec.limit} primeros)"
    filters = spec.filter_labels()
    if filters:
        what += ". Filtros: " + "; ".join(filters)
    return f"{spec.intent}: {what}."


def _text(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10] if not isinstance(value, datetime.datetime) else value.isoformat(sep=" ")
    if isinstance(value, (float, Decimal)):
        return format_number(value)
    return str(value)


def _plain(value):
    """JSON-safe value."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _plain_spec(spec):
    data = spec.to_dict()

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return _plain(v)
    return walk(data)
