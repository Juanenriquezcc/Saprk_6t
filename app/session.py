"""State of one analysis session: dataset, profile, roles, column choices and history.

The history keeps only small things (question, spec, SQL, bounded result rows,
timestamp); never the dataset. Every operation (question, guided analysis, full
analysis, manual SQL) ends up as an Evidence in the same history.
"""
from dataclasses import dataclass, field

import config
from app.query.executor import check_read_only, execute
from app.questions.intents import Interpreter, NeedsInput
from app.questions.parser import ParsedQuestion, parse_question
from app.questions.resolver import evidence_for_sql, run_spec, solve
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

    def __post_init__(self):
        self.interpreter = Interpreter(self.profile, self.semantic, self.choices)

    @classmethod
    def start(cls, spark, load):
        profile = profile_dataset(load.df, load.rows)
        return cls(spark=spark, load=load, profile=profile, semantic=detect_roles(profile))

    @property
    def column_names(self):
        return [c.name for c in self.profile.columns]

    def ask(self, text, choose):
        """Solves a question. `choose(need)` returns the chosen value or None to cancel.

        Returns Evidence, or NeedsInput when the question cannot be interpreted.
        """
        parsed = parse_question(text)
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

    def run_spec(self, spec, label):
        """Guided / full analysis: a QuerySpec built by the program, same builder and evidence."""
        parsed = ParsedQuestion(raw=label, body=label, normalized="")
        return self._record(run_spec(self.spark, parsed, spec, self.column_names))

    def run_sql(self, sql):
        """Manual read-only SQL over the `dataset` view, bounded result."""
        statement = check_read_only(sql)
        result = execute(self.spark, statement, max_rows=config.MANUAL_SQL_MAX_ROWS, known_columns=self.column_names)
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
