"""ETL pipeline: EXTRACT -> TRANSFORM -> VALIDATE -> LOAD.

  EXTRACT   app/spark/loader.py   (CSV / JSON / Parquet, already done when this runs)
  TRANSFORM app/etl/transformer.py
  VALIDATE  app/etl/validator.py + the lab rules (app/etl/rules.py)
  LOAD      here: the clean data is cached and registered as the `dataset` view; the data
            as read stays in `dataset_original` and the rejected rows (with their reason)
            in `rechazados`, so every number of the report can be checked with SQL. A catalog
            table uses its own names instead (etl_views).

Every decision the data cannot settle goes through `decide(Decision) -> value`
(the terminal asks the user; tests pass fixed answers). Nothing is chosen silently.
"""
import re
import time
from dataclasses import dataclass, field

import config
from app.errors import AppError
from app.etl import rules as rl
from app.etl import transformer as tf
from app.etl import validator as vd
from app.etl.transformer import Decision


@dataclass
class EtlReport:
    original_rows: int
    valid_rows: int
    rejected_rows: int = 0
    duplicates_found: int = 0
    duplicates_removed: int = 0
    columns: list = field(default_factory=list)
    transformations: list = field(default_factory=list)   # [Transformation]
    rules: list = field(default_factory=list)             # [Rule]
    rule_results: list = field(default_factory=list)      # [RuleResult]
    invalid_values: dict = field(default_factory=dict)    # column -> values not convertible
    decisions: dict = field(default_factory=dict)         # key -> chosen value
    reject_unknown: bool = None
    rules_source: str = None
    warnings: list = field(default_factory=list)
    null_counts: dict = field(default_factory=dict)       # filled from the profile of the clean data
    seconds: float = 0.0
    df: object = None                                     # clean DataFrame (not exported)
    views: tuple = (config.VIEW_NAME, config.RAW_VIEW_NAME, config.REJECTED_VIEW_NAME)   # see etl_views()
    similar_values: list = field(default_factory=list)    # 'Laptop Pro14' / 'Laptop Pro 14': reported, not merged

    @property
    def changed(self):
        return bool(self.transformations or self.rules or self.rejected_rows or self.duplicates_removed)

    def checklist(self):
        """Short lines for the screen: what was done."""
        kinds = {t.kind for t in self.transformations}
        lines = [("Texto normalizado (espacios y vacios)", bool(kinds & {"trim", "nulls"})),
                 ("Categorias unificadas", "category" in kinds),
                 ("Tipos convertidos (numeros escritos como texto)", "number" in kinds),
                 ("Fechas normalizadas", "date" in kinds),
                 ("Columnas derivadas calculadas", any(r.kind == rl.DERIVED for r in self.rules)),
                 ("Duplicados revisados", True),
                 ("Reglas de calidad validadas", any(r.kind == rl.CHECK for r in self.rules))]
        return lines

    def to_dict(self):
        return {
            "registros_originales": self.original_rows,
            "registros_validos": self.valid_rows,
            "registros_rechazados": self.rejected_rows,
            "duplicados_encontrados": self.duplicates_found,
            "duplicados_eliminados": self.duplicates_removed,
            "columnas": self.columns,
            "transformaciones": [t.to_dict() for t in self.transformations],
            "reglas": [r.to_dict() for r in self.rules],
            "resultado_reglas": [r.to_dict() for r in self.rule_results],
            "valores_no_validos": self.invalid_values,
            "decisiones": self.decisions,
            "rechazar_reglas_con_nulos": self.reject_unknown,
            "origen_reglas": self.rules_source,
            "nulos_por_columna": self.null_counts,
            "avisos": self.warnings,
            "posibles_equivalencias_no_aplicadas": self.similar_values,
            "vistas_sql": dict(zip(("limpio", "original", "rechazados"), self.views)),
            "segundos": round(self.seconds, 2),
        }


def etl_views(view=config.VIEW_NAME):
    """(clean, original, rejected) view names. `dataset` keeps its historic names; a catalog
    table 'pedidos' gets pedidos / pedidos_original / pedidos_rechazados."""
    if view == config.VIEW_NAME:
        return config.VIEW_NAME, config.RAW_VIEW_NAME, config.REJECTED_VIEW_NAME
    return view, f"{view}_original", f"{view}_rechazados"


def run(spark, load, rules=(), decide=None, rules_source=None, view=config.VIEW_NAME):
    """Runs the ETL over `load.df` (already read and cached) and registers the views
    etl_views(view). Returns EtlReport."""
    from pyspark.sql import functions as F

    start = time.perf_counter()
    raw = load.df
    decide = decide or _no_decisions
    rules = list(rules)
    report = EtlReport(original_rows=load.rows, valid_rows=load.rows, rules=rules, rules_source=rules_source,
                       views=etl_views(view))

    # --- TRANSFORM -----------------------------------------------------------------
    transformations, decisions, report.similar_values = tf.detect(raw, load)
    if transformations:
        choice = decide(Decision("transform", "Se proponen estas transformaciones de limpieza. Aplicarlas?",
                                 [("Aplicar todas (recomendado)", "all"), ("No aplicar ninguna", "none")],
                                 [t.description + (f" ({t.affected} valores)" if t.affected else "") for t in transformations]))
        if choice != "all":
            transformations, decisions = [], []
    for d in decisions:
        report.decisions[d.key] = decide(d)
    # '12.500' confirmed as a decimal number: Spark already read it right, nothing to do.
    report.transformations = transformations = [
        t for t in transformations if not (t.kind == "scale" and report.decisions.get(f"number:{t.column}") == "decimal")]
    try:
        df = tf.apply(raw, transformations, report.decisions)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    df = _equivalences(df, [r for r in rules if r.kind == rl.EQUIVALENCE], decide, report)

    for rule in (r for r in rules if r.kind == rl.DERIVED):
        if rule.name.lower() in (c.lower() for c in df.columns):
            raise AppError(f"La columna derivada '{rule.name}' ya existe en el dataset.", "Use otro nombre.")
        rl.check_rule(df, rule)
        df = df.withColumn(rule.name, F.expr(rule.expr))

    # --- VALIDATE ------------------------------------------------------------------
    checks = [r for r in rules if r.kind == rl.CHECK]
    for rule in checks:
        rl.check_rule(df, rule)
    try:
        report.rule_results, report.invalid_values = vd.evaluate(df, checks)
    except Exception as exc:
        from app.query.executor import translate_error
        raise translate_error(exc, df.columns) from exc
    unknown = sum(r.unknown for r in report.rule_results)
    report.reject_unknown = False
    if unknown:
        report.reject_unknown = decide(Decision(
            "unknown", f"{unknown} evaluacion(es) de reglas no se pueden hacer porque el registro tiene valores "
                       "vacios en esas columnas. Que hacer con esos registros?",
            [("Rechazarlos (el valor es obligatorio)", True), ("Conservarlos (el valor vacio se permite)", False)],
            [f"{r.text}: {r.unknown} sin evaluar" for r in report.rule_results if r.unknown]))
        report.decisions["unknown"] = report.reject_unknown

    # Cached once: the rejected count, the duplicate check and the final cache all read it, and
    # without it each would redo every text conversion. Released when the clean data is cached.
    checked = vd.with_reason(df, checks, report.reject_unknown).cache()
    visible = [c for c in df.columns if not c.startswith(tf.RAW_PREFIX)]
    rejected = checked.where(F.col(vd.REASON_COL) != "").select(*[tf.q(c) for c in visible], vd.REASON_COL)
    valid = checked.where(F.col(vd.REASON_COL) == "").select(*[tf.q(c) for c in visible])
    report.rejected_rows = rejected.count() if (checks or report.invalid_values) else 0

    distinct = valid.dropDuplicates(visible).count()
    report.duplicates_found = (load.rows - report.rejected_rows) - distinct
    if report.duplicates_found:
        remove = decide(Decision("duplicates", f"Hay {report.duplicates_found} registro(s) duplicado(s) exacto(s) "
                                               "(todas las columnas iguales). Que hacer?",
                                 [("Eliminar los duplicados (conservar una copia)", True),
                                  ("Conservarlos (el taller los cuenta)", False)]))
        report.decisions["duplicates"] = remove
        if remove:
            valid = valid.dropDuplicates(visible)
            report.duplicates_removed = report.duplicates_found

    # --- LOAD ----------------------------------------------------------------------
    report.valid_rows = load.rows - report.rejected_rows - report.duplicates_removed
    if report.valid_rows == 0:
        raise AppError("Despues del ETL no queda ningun registro valido.",
                       "Revise las reglas de calidad: puede haber una regla mal escrita.")
    if report.changed:
        clean = valid.cache()
        report.valid_rows = clean.count()   # materializes the cache once
        raw.unpersist()          # the original stays readable through its file (view dataset_original)
    else:
        clean = raw
    checked.unpersist()
    clean_view, raw_view, rejected_view = report.views
    raw.createOrReplaceTempView(raw_view)
    rejected.createOrReplaceTempView(rejected_view)
    clean.createOrReplaceTempView(clean_view)
    report.df = clean
    report.columns = list(clean.columns)
    report.seconds = time.perf_counter() - start
    return report


# A code or reference: no spaces, at least one digit, letters/digits joined by - _ . / ('R001', 'R-001').
CODE = r"(?=[^\s]*\d)[A-Za-z0-9]+(?:[-_./][A-Za-z0-9]+)*"
CODE_COLUMN_RATIO = 0.8     # share of code-shaped values that makes a column a code column


def _equivalences(df, rules, decide, report):
    """Equivalences of the case ('equivalencia: product: Laptop Pro14 -> Laptop Pro 14'): each one is
    shown with its counts and applied only when the user confirms it. Never on identifiers, codes
    or non-text columns. Matching ignores case, accents and outer/double spaces (as the categories)."""
    if not rules:
        return df
    from pyspark.sql import functions as F
    from app.schema.profiler import ID_TOKENS
    from app.text import name_tokens

    types = dict(df.dtypes)
    columns = sorted({r.name for r in rules})
    for r in rules:
        if r.name not in types:
            raise AppError(f"La equivalencia '{r.text}' usa la columna '{r.name}', que no existe.",
                           "Columnas: " + ", ".join(c for c in df.columns if not c.startswith(tf.RAW_PREFIX)))
        if types[r.name] != "string" or set(name_tokens(r.name)) & ID_TOKENS:
            raise AppError(f"La equivalencia '{r.text}' no se aplica: '{r.name}' no es una columna de texto "
                           "descriptivo (identificadores, codigos y numeros no se unifican).")
    # One aggregation: rows of every spelling, and how unique each column is (codes are near-unique).
    exprs = [F.count_if(tf.text_key(r.name) == F.lit(tf.category_key(v))).alias(f"e{i}_{side}")
             for i, r in enumerate(rules) for side, v in enumerate(r.values)]
    exprs += [e for j, c in enumerate(columns) for e in (F.approx_count_distinct(F.col(tf.q(c))).alias(f"d{j}"),
                                                         F.count(F.col(tf.q(c))).alias(f"n{j}"),
                                                         F.count_if(F.trim(F.col(tf.q(c))).rlike(f"^{CODE}$")).alias(f"c{j}"))]
    stats = df.agg(*exprs).first()
    for j, c in enumerate(columns):
        if stats[f"n{j}"] > 1 and stats[f"d{j}"] >= config.ID_MIN_UNIQUE_RATIO * stats[f"n{j}"]:
            raise AppError(f"La columna '{c}' tiene un valor distinto en casi cada registro (identificador o "
                           "codigo): no se le aplican equivalencias.")
        # Repeated rows can lower the unique ratio, so the shape of the values decides too (F2).
        if stats[f"n{j}"] and stats[f"c{j}"] >= CODE_COLUMN_RATIO * stats[f"n{j}"]:
            raise AppError(f"La columna '{c}' contiene codigos o referencias ({stats[f'c{j}']} de {stats[f'n{j}']} "
                           "valores tienen forma de codigo): no se le aplican equivalencias.")
    for r in rules:
        coded = [v for v in r.values if re.fullmatch(CODE, v.strip())]
        if coded:
            raise AppError(f"La equivalencia '{r.text}' no se aplica: {coded[0]!r} tiene forma de codigo "
                           "(letras y digitos sin espacios). Los codigos distintos nunca se unifican.")
    for i, r in enumerate(rules):
        (old, new), found, target = r.values, stats[f"e{i}_0"], stats[f"e{i}_1"]
        key = f"equivalence:{r.name}:{old}"
        if not found:
            report.warnings.append(f"Equivalencia sin efecto: {old!r} no aparece en {r.name}.")
            continue
        details = [f"{r.name}: {old!r} ({found} registro(s)) -> {new!r} ({target} registro(s))"]
        if not target:
            details.append(f"Atencion: {new!r} no aparece en los datos; sera un valor nuevo.")
        report.decisions[key] = decide(Decision(
            key, f"Equivalencia del caso: {old!r} y {new!r} son el mismo valor de {r.name}?",
            [("Si: unificar (reemplazar por " + repr(new) + ")", True), ("No: dejarlos separados", False)], details))
        if not report.decisions[key]:
            report.warnings.append(f"Equivalencia no confirmada (no aplicada): {r.name}: {old!r} -> {new!r}.")
            continue
        df = df.withColumn(r.name, F.when(tf.text_key(r.name) == F.lit(tf.category_key(old)), F.lit(new))
                           .otherwise(F.col(tf.q(r.name))))
        report.transformations.append(tf.Transformation(
            "equivalence", r.name, f"{r.name}: equivalencia confirmada {old!r} -> {new!r}", found, details,
            {"from": old, "to": new}))
    return df


def _no_decisions(decision):
    raise AppError("El ETL necesita una decision del usuario: " + decision.message)
