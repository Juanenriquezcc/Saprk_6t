"""Full analysis: a bounded plan of useful queries chosen from the profile and roles.

Every step is a QuerySpec (or the read-only DESCRIBE statement) run through the same
builder / executor / evidence. The plan never exceeds config.FULL_ANALYSIS_MAX_STEPS.
"""
from dataclasses import dataclass

import config
from app.analysis import guided
from app.errors import AppError
from app.query import spec as s
from app.query.spec import Condition, Metric, QuerySpec

# Roles tried, in order, to choose the main metric and the main entity of the dataset.
METRIC_ROLES = ("VOLUME", "CLOSE", "TOTAL", "AMOUNT", "QUANTITY", "PRICE")
ENTITY_ROLES = ("COMPANY", "PRODUCT", "CATEGORY", "NAME")


@dataclass
class Step:
    label: str
    spec: QuerySpec = None        # None -> `sql` (read-only statement)
    sql: str = None


@dataclass
class StepResult:
    step: Step
    evidence: object = None
    error: AppError = None


def plan(profile, semantic):
    numeric = [c.name for c in profile.columns if c.kind == "numeric" and not c.is_id]
    limit_cols = config.FULL_ANALYSIS_MAX_COLUMNS
    top_n = config.FULL_ANALYSIS_TOP_N
    steps = [
        Step("Conteo de registros", QuerySpec(s.COUNT_ROWS, s.SCALAR, "COUNT")),
        Step("Columnas y tipos", sql=f"DESCRIBE {config.VIEW_NAME}"),
        Step("Nulos por columna", QuerySpec(s.COLUMN_SUMMARY, s.SCALAR, "SUMMARY",
                                            columns=[c.name for c in profile.columns][:limit_cols * 2],
                                            stats=["no_nulos", "nulos"])),
    ]
    if numeric:
        steps.append(Step("Perfil numerico (min, max, promedio, desviacion)",
                          QuerySpec(s.COLUMN_SUMMARY, s.SCALAR, "SUMMARY", columns=numeric[:limit_cols],
                                    stats=["minimo", "maximo", "promedio", "desviacion"])))
    text_cols = [c.name for c in profile.columns if c.kind in ("text", "boolean") or c.is_date]
    if text_cols:
        steps.append(Step("Cardinalidad de columnas no numericas",
                          QuerySpec(s.COLUMN_SUMMARY, s.SCALAR, "SUMMARY", columns=text_cols[:limit_cols],
                                    stats=["distintos"])))
    for col in profile.categorical[: config.FULL_ANALYSIS_CATEGORY_COLUMNS]:
        steps.append(Step(f"Principales valores de {col.name}",
                          QuerySpec(s.GROUP_AGG, s.GROUP, "COUNT", group_by=col.name, order="DESC", limit=top_n)))

    metric = _first_role(semantic, METRIC_ROLES) or (numeric[0] if numeric else None)
    entity = _first_role(semantic, ENTITY_ROLES, profile=profile) or (
        profile.categorical[0].name if profile.categorical else None)
    if metric and entity:
        steps.append(Step(f"Ranking: {entity} con mayor suma de {metric}",
                          guided.ranking(entity, "SUM", metric, "DESC", top_n)))
    if metric:
        steps.append(Step(f"Registro con mayor {metric}", guided.extreme(metric, "MAX", "record")))

    date_col = semantic.resolved("DATE")
    if date_col:
        steps.append(Step(f"Rango de fechas ({date_col})",
                          QuerySpec(s.COLUMN_SUMMARY, s.SCALAR, "SUMMARY", columns=[date_col], stats=["minimo", "maximo"])))
    # Relations that make sense only when the semantic roles exist.
    open_col, close_col = semantic.resolved("OPEN"), semantic.resolved("CLOSE")
    if open_col and close_col:
        steps.append(Step(f"Registros con {close_col} > {open_col}",
                          guided.count_where([Condition(close_col, ">", other_column=open_col)])))
        steps.append(Step(f"Variacion % promedio de {open_col} a {close_col}",
                          guided.pct_change(open_col, close_col, "AVG")))
    high_col, low_col = semantic.resolved("HIGH"), semantic.resolved("LOW")
    if high_col and low_col:
        steps.append(Step(f"Mayor rango {high_col} - {low_col}",
                          QuerySpec(s.DIFFERENCE, s.SCALAR, "MAX", Metric("difference", (high_col, low_col)))))
    if metric and entity:   # lowest priority: dropped first when the plan is over the limit
        steps.append(Step(f"Ranking: {entity} con mayor promedio de {metric}",
                          guided.ranking(entity, "AVG", metric, "DESC", top_n)))
    return steps[: config.FULL_ANALYSIS_MAX_STEPS]


def run(session, progress=None, on_result=None):
    """Runs the plan; a failing step is reported and the analysis continues."""
    steps = plan(session.profile, session.semantic)
    results = []
    for i, step in enumerate(steps, 1):
        if progress:
            progress(i, len(steps), step.label)
        label = f"Analisis completo: {step.label}"
        try:
            if step.sql:
                evidence = session.run_sql(step.sql)
                evidence.question = label
            else:
                evidence = session.run_spec(step.spec, label)
            result = StepResult(step, evidence=evidence)
        except AppError as exc:
            result = StepResult(step, error=exc)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def _first_role(semantic, roles, profile=None):
    for role in roles:
        column = semantic.resolved(role)
        if column and (profile is None or profile.column(column).is_categorical):
            return column
    return None
