"""Answer validation: compares the student's answer with the answer computed by Spark.

Status of every workshop question:
  CORRECTA        the student's answer is the computed one
  INCORRECTA      it is not
  SIN RESPUESTA   computed, but the student gave no answer to check
  NO DETERMINADA  computed, but the correct option/verdict cannot be decided
                  (no option matches, several match, ambiguous number) - never forced
  NO RESUELTA     the question could not be interpreted or the query failed
"""
import re

from app.query import spec as s
from app.questions.resolver import _match_precision

CORRECT = "CORRECTA"
INCORRECT = "INCORRECTA"
NO_ANSWER = "SIN RESPUESTA"
UNDETERMINED = "NO DETERMINADA"
UNRESOLVED = "NO RESUELTA"
STATUSES = (CORRECT, INCORRECT, NO_ANSWER, UNDETERMINED, UNRESOLVED)

CLAIM_OPS = {">": "mayor a ", "<": "menor a ", ">=": "mayor o igual a ", "<=": "menor o igual a "}


def claim_text(ev):
    """'mayor a 500000' / '15': the claimed value as the student wrote it."""
    return f"{CLAIM_OPS.get(ev.claim_op, '')}{ev.claim}"


_TRUE = {"v", "verdadero", "verdadera", "true", "t", "si", "cierto"}
_FALSE = {"f", "falso", "falsa", "false", "no"}


def read_letter(text, options):
    """'b', 'B)', 'opcion b' -> 'B' when B is an option; None otherwise."""
    m = re.fullmatch(r"\s*(?:opcion\s+|opción\s+)?\(?([A-Za-z])\)?[.)]?\s*", text or "")
    letter = m.group(1).upper() if m else None
    return letter if letter in {l for l, _ in options} else None


def read_verdict(text):
    word = (text or "").strip().lower().rstrip(".")
    return "VERDADERO" if word in _TRUE else ("FALSO" if word in _FALSE else None)


def validate(ev, selected):
    """Fills the validation fields of an Evidence. `selected` = the student's answer or None."""
    selected = (selected or "").strip() or None
    options = dict(tuple(o) for o in ev.options)
    if ev.question_type == s.MULTIPLE_CHOICE:
        letter = read_letter(selected, ev.options) if selected else None
        ev.selected_answer = f"{letter}) {options[letter]}" if letter else selected
        ev.correct_answer = f"{ev.matched_option}) {options[ev.matched_option]}" if ev.matched_option else None
        if ev.matched_option is None:
            ev.validation = UNDETERMINED
            ev.validation_note = (f"La consulta calculo {ev.result_text}, que no coincide con ninguna opcion "
                                  "(o coincide con varias). No se fuerza una respuesta.")
        elif not selected:
            ev.validation = NO_ANSWER
            ev.validation_note = f"La respuesta correcta es {ev.correct_answer}."
        else:
            ev.validation = CORRECT if letter == ev.matched_option else INCORRECT
            ev.validation_note = (f"La consulta calculo {ev.result_text} -> opcion {ev.correct_answer}. "
                                  f"La opcion seleccionada fue {ev.selected_answer}.")
            if letter is None:
                ev.validation_note += f" ('{selected}' no es una de las opciones.)"
    elif ev.question_type == s.TRUE_FALSE:
        verdict = read_verdict(selected) if selected else None
        ev.selected_answer = verdict or selected
        ev.correct_answer = ev.verdict
        if ev.verdict is None:
            ev.validation = UNDETERMINED
            ev.validation_note = "No se pudo decidir si la afirmacion es verdadera o falsa (ver advertencias)."
        elif not selected:
            ev.validation = NO_ANSWER
            ev.validation_note = f"La afirmacion es {ev.verdict} (valor calculado: {ev.correct_value})."
        else:
            ev.validation = CORRECT if verdict == ev.verdict else INCORRECT
            ev.validation_note = (f"Valor afirmado: {claim_text(ev)} | valor calculado: {ev.correct_value} -> {ev.verdict}. "
                                  f"El estudiante respondio {ev.selected_answer}.")
    else:
        ev.selected_answer = selected
        ev.correct_answer = ev.result_text
        if not selected:
            ev.validation = NO_ANSWER
            ev.validation_note = f"Resultado calculado: {ev.result_text}."
        else:
            candidates = [ev.value] + (list(ev.result_rows[0]) if ev.result_rows else [])
            ok = any(_match_precision(selected, v) is not None for v in candidates if v is not None)
            ev.validation = CORRECT if ok else INCORRECT
            ev.validation_note = f"La consulta calculo {ev.result_text}. El estudiante respondio {selected}."
    return ev
