"""VALIDATE: data quality checks with Spark. Every row gets a reason when it fails.

Checks:
  - the lab rules (app/etl/rules.py), e.g. quantity BETWEEN 1 AND 20;
  - values that could not be converted by the transformer (a 'abc' in a numeric column).
A rule whose result is NULL (it uses an empty value) is 'unknown': the user decides once
whether those rows are rejected or kept. One aggregation counts everything.
"""
from dataclasses import dataclass

from app.etl.transformer import RAW_PREFIX, clean_text, q

REASON_COL = "motivo_rechazo"


@dataclass
class RuleResult:
    text: str
    expr: str
    failed: int = 0       # rows where the rule is false
    unknown: int = 0      # rows where it cannot be evaluated (NULL values)

    def to_dict(self):
        return {"regla": self.text, "expresion": self.expr, "incumplen": self.failed, "sin_evaluar_por_nulos": self.unknown}


def converted_columns(df):
    return [c[len(RAW_PREFIX):] for c in df.columns if c.startswith(RAW_PREFIX)]


def _invalid(col):
    """The original text had a value but the converted column is NULL."""
    from pyspark.sql import functions as F
    return clean_text(RAW_PREFIX + col).isNotNull() & F.col(q(col)).isNull()


def evaluate(df, checks):
    """([RuleResult], {column: invalid values}) in one Spark job."""
    from pyspark.sql import functions as F

    converted = converted_columns(df)
    exprs = []
    for i, rule in enumerate(checks):
        cond = F.expr(rule.expr)
        exprs += [F.count_if(~cond).alias(f"f_{i}"), F.count_if(cond.isNull()).alias(f"u_{i}")]
    exprs += [F.count_if(_invalid(c)).alias(f"inv_{i}") for i, c in enumerate(converted)]
    if not exprs:
        return [], {}
    row = df.agg(*exprs).first()
    results = [RuleResult(r.text, r.expr, row[f"f_{i}"], row[f"u_{i}"]) for i, r in enumerate(checks)]
    invalid = {c: row[f"inv_{i}"] for i, c in enumerate(converted) if row[f"inv_{i}"]}
    return results, invalid


def with_reason(df, checks, reject_unknown):
    """Adds REASON_COL: '' for valid rows, otherwise every failed check separated by '; '."""
    from pyspark.sql import functions as F

    parts = []
    for rule in checks:
        ok = F.coalesce(F.expr(rule.expr), F.lit(not reject_unknown))
        parts.append(F.when(~ok, F.lit(f"regla: {rule.text}")))
    for c in converted_columns(df):
        parts.append(F.when(_invalid(c), F.lit(f"valor no valido en {c}")))
    reason = F.concat_ws("; ", *parts) if parts else F.lit("")
    return df.withColumn(REASON_COL, reason)
