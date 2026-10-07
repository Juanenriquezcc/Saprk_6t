"""ETL pipeline: EXTRACT -> TRANSFORM -> VALIDATE -> LOAD.

  EXTRACT   app/spark/loader.py   (CSV / JSON / Parquet, already done when this runs)
  TRANSFORM app/etl/transformer.py
  VALIDATE  app/etl/validator.py + the lab rules (app/etl/rules.py)
  LOAD      here: the clean data is cached and registered as the `dataset` view; the data
            as read stays in `dataset_original` and the rejected rows (with their reason)
            in `rechazados`, so every number of the report can be checked with SQL.

Every decision the data cannot settle goes through `decide(Decision) -> value`
(the terminal asks the user; tests pass fixed answers). Nothing is chosen silently.
"""
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
            "vistas_sql": {"limpio": config.VIEW_NAME, "original": config.RAW_VIEW_NAME,
                           "rechazados": config.REJECTED_VIEW_NAME},
            "segundos": round(self.seconds, 2),
        }


def run(spark, load, rules=(), decide=None, rules_source=None):
    """Runs the ETL over `load.df` (already read and cached). Returns EtlReport."""
    from pyspark.sql import functions as F

    start = time.perf_counter()
    raw = load.df
    decide = decide or _no_decisions
    rules = list(rules)
    report = EtlReport(original_rows=load.rows, valid_rows=load.rows, rules=rules, rules_source=rules_source)

    # --- TRANSFORM -----------------------------------------------------------------
    transformations, decisions = tf.detect(raw, load)
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
    raw.createOrReplaceTempView(config.RAW_VIEW_NAME)
    rejected.createOrReplaceTempView(config.REJECTED_VIEW_NAME)
    clean.createOrReplaceTempView(config.VIEW_NAME)
    report.df = clean
    report.columns = list(clean.columns)
    report.seconds = time.perf_counter() - start
    return report


def _no_decisions(decision):
    raise AppError("El ETL necesita una decision del usuario: " + decision.message)
