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
from app.catalog import DatasetCatalog, describe
from app.questions.joins import CatalogInterpreter
from app.schema.values import ValueLookup

DATASET_SCOPE, CATALOG_SCOPE, AUTO_SCOPE = "dataset", "catalogo", "automatico"


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
    # Additional tables, each in its own view. The dataset above (view `dataset`) is the active
    # one: questions, guided analysis and the workshop work on it only.
    catalog: DatasetCatalog = field(default_factory=DatasetCatalog)
    # Where questions are answered: the active dataset, the catalog tables (combined through confirmed
    # relations, app/questions/joins.py) or automatico (default): the catalog when the active dataset is
    # one of its tables and there are several, so each question finds its own tables; otherwise the
    # active dataset, exactly as before. An explicit choice of the user is never changed silently.
    scope: str = AUTO_SCOPE
    active_alias: str = None      # catalog alias of the active dataset (when it is part of the catalog)
    catalog_choices: dict = field(default_factory=dict)   # remembered choices of catalog questions
    catalog_interpreter: CatalogInterpreter = None

    def __post_init__(self):
        self.interpreter = Interpreter(self.profile, self.semantic, self.choices,
                                       value_lookup=ValueLookup(lambda: [(self.df, self.profile)]))
        self.catalog_interpreter = CatalogInterpreter(self.catalog, self.catalog_choices)

    @property
    def uses_catalog(self):
        if self.scope == CATALOG_SCOPE:
            return True
        return self.scope == AUTO_SCOPE and self.active_alias in self.catalog and len(self.catalog) > 1

    @property
    def active_interpreter(self):
        return self.catalog_interpreter if self.uses_catalog else self.interpreter

    @classmethod
    def from_catalog(cls, spark, catalog, alias):
        """Session of a workshop with several datasets: `alias` is the active one (view `dataset`)."""
        entry = catalog.get(alias)
        entry.df.createOrReplaceTempView(config.VIEW_NAME)
        return cls(spark=spark, load=entry.load, profile=entry.profile, semantic=entry.semantic, etl=entry.etl,
                   catalog=catalog, active_alias=entry.alias)

    def activate(self, alias):
        """Another catalog table becomes the active dataset (view `dataset`): nothing is read again."""
        entry = self.catalog.get(alias)
        entry.df.createOrReplaceTempView(config.VIEW_NAME)
        self.load, self.profile, self.semantic, self.etl = entry.load, entry.profile, entry.semantic, entry.etl
        self.active_alias = entry.alias
        self.choices.clear()
        self.interpreter = Interpreter(self.profile, self.semantic, self.choices,
                                       value_lookup=ValueLookup(lambda: [(self.df, self.profile)]))

    @classmethod
    def start(cls, spark, load, etl=None):
        profile, semantic = describe(load, etl)
        return cls(spark=spark, load=load, profile=profile, semantic=semantic, etl=etl)

    @property
    def relations(self):
        """Relations among the catalog tables (the active `dataset` is not part of the catalog)."""
        return self.catalog.relations

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
        interpreter = self.active_interpreter
        extra, clarifications = {}, []
        while True:
            outcome = solve(self.spark, interpreter, parsed, extra, number=self._next_number(parsed))
            if not isinstance(outcome, NeedsInput):
                outcome.clarifications = clarifications
                return self._record(outcome)
            if not outcome.options:
                return outcome
            value = choose(outcome)
            if value is None:
                return None
            label = next((l for l, v in outcome.options if v == value), str(value))
            clarifications.append(f"{outcome.message} -> {label}")
            if outcome.remember:
                interpreter.choices[outcome.key] = value
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
