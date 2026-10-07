"""State of one analysis session: dataset, profile, roles, column choices and history.

The history keeps only small things (question, spec, SQL, bounded result rows,
timestamp); never the dataset. Every operation (question, guided analysis, full
analysis, manual SQL) ends up as an Evidence in the same history.
"""
import datetime
from dataclasses import dataclass, field

import config
from app.errors import AppError
from app.query import spec as s
from app.query.executor import check_read_only, execute
from app.questions.intents import Interpreter, NeedsInput
from app.questions.parser import ParsedQuestion, parse_question
from app.text import normalize
from app.questions.resolver import Evidence, evidence_for_sql, run_spec, solve
from app.questions.validator import UNRESOLVED, validate
from app.schema.profiler import profile_dataset
from app.schema.semantic import detect_roles


@dataclass
class LabSession:
    spark: object
    load: object                  # LoadResult
    profile: object
    semantic: object
    choices: dict = field(default_factory=dict)    # remembered column choices
    history: list = field(default_factory=list)    # [Evidence]
    interpreter: Interpreter = None
    etl: object = None            # EtlReport when the data went through the ETL
    sql_errors: int = 0           # failed queries (shown in the workshop summary)

    def __post_init__(self):
        self.interpreter = Interpreter(self.profile, self.semantic, self.choices)

    @classmethod
    def start(cls, spark, load, etl=None):
        df, rows = (etl.df, etl.valid_rows) if etl else (load.df, load.rows)
        profile = profile_dataset(df, rows)
        if etl:
            etl.null_counts = {c.name: c.nulls for c in profile.columns}
        return cls(spark=spark, load=load, profile=profile, semantic=detect_roles(profile), etl=etl)

    @property
    def df(self):
        return self.etl.df if self.etl else self.load.df

    @property
    def column_names(self):
        return [c.name for c in self.profile.columns]

    def ask(self, text, choose, kind=None):
        """Solves a question. `choose(need)` returns the chosen value or None to cancel.

        Returns Evidence, or NeedsInput when the question cannot be interpreted.
        """
        parsed = force_kind(parse_question(text), kind)
        extra = {}
        while True:
            outcome = solve(self.spark, self.interpreter, parsed, extra, number=self._next_number(parsed))
            if not isinstance(outcome, NeedsInput):
                return self._record(outcome)
            if not outcome.options:
                return outcome
            value = choose(outcome)
            if value is None:
                return None
            if outcome.remember:
                self.choices[outcome.key] = value
            else:
                extra[outcome.key] = value

    def solve_question(self, text, choose, kind=None, selected=None):
        """Workshop question: solve, validate the student's answer and ALWAYS record it.

        `kind` forces the type chosen by the user (OPEN / MULTIPLE_CHOICE / TRUE_FALSE).
        A question that cannot be solved is recorded as NO RESUELTA with the reason.
        """
        try:
            outcome = self.ask(text, choose, kind=kind)
        except AppError as exc:
            self.sql_errors += 1
            return self._unresolved(text, kind, exc.message, [exc.hint] if exc.hint else [])
        if outcome is None:
            return self._unresolved(text, kind, "Pregunta cancelada por el usuario.")
        if isinstance(outcome, NeedsInput):
            return self._unresolved(text, kind, outcome.message, [f"Falta: {m}" for m in outcome.missing])
        return validate(outcome, selected)

    def _unresolved(self, text, kind, reason, warnings=()):
        parsed = parse_question(text)
        ev = Evidence(question=text.strip(), question_type=kind or parsed.kind, intent="NO_RESUELTA",
                      columns_used=[], filters=[], sql="", result_columns=[], result_rows=[], value=None,
                      answer="NO RESUELTA", result_text="Sin resultado", interpretation=reason,
                      timestamp=datetime.datetime.now().isoformat(timespec="seconds"), seconds=0.0,
                      warnings=list(warnings), options=[list(o) for o in parsed.options], claim=parsed.claim,
                      validation=UNRESOLVED, validation_note=reason)
        ev.number = self._next_number(parsed)
        self.history.append(ev)
        return ev

    @property
    def questions(self):
        """Workshop questions (not guided analysis nor manual SQL)."""
        return [e for e in self.history if e.source == "pregunta"]

    def run_spec(self, spec, label):
        """Guided / full analysis: a QuerySpec built by the program, same builder and evidence."""
        parsed = ParsedQuestion(raw=label, body=label, normalized="")
        evidence = run_spec(self.spark, parsed, spec, self.column_names)
        evidence.source = "analisis"
        return self._record(evidence)

    def run_sql(self, sql):
        """Manual read-only SQL over the `dataset` view, bounded result."""
        statement = check_read_only(sql)
        try:
            result = execute(self.spark, statement, max_rows=config.MANUAL_SQL_MAX_ROWS, known_columns=self.column_names)
        except AppError:
            self.sql_errors += 1
            raise
        return self._record(evidence_for_sql(statement, result))

    def _record(self, evidence):
        if evidence.number is None:
            evidence.number = self._next_number(None)
        self.history.append(evidence)
        return evidence

    def _next_number(self, parsed):
        if parsed is not None and parsed.number is not None:
            return parsed.number
        used = [e.number for e in self.history if e.number is not None]
        return (max(used) + 1) if used else 1


def force_kind(parsed, kind):
    """Applies the question type chosen by the user over the detected one."""
    if kind is None or kind == parsed.kind:
        return parsed
    if kind == s.OPEN:
        # Read as an open question: the 'claimed value' is part of the text again.
        text = parsed.body + (f" {parsed.claim}" if parsed.claim else "")
        parsed.body, parsed.normalized, parsed.claim, parsed.claim_op = text, normalize(text), None, "="
        parsed.options = []
    parsed.kind = kind
    return parsed
