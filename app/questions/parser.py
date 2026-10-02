"""Splits a raw lab question into: number, body, options (A-E) and claim (true/false)."""
import re
from dataclasses import dataclass, field

from app.query import spec as s
from app.text import normalize, strip_accents

_NUMBER_PREFIX = re.compile(r"^\s*(?:p|pregunta|question|q)?\s*(\d{1,3})\s*[.:)\-]\s+", re.IGNORECASE)
_OPTION_LINE = re.compile(r"^\s*\(?([A-Ea-e])\s*[).:\-]\s*(.+?)\s*$")
_INLINE_OPTION = re.compile(r"(?:^|\s)\(?([A-E])\)\s*")
_TF_PREFIX = re.compile(
    r"^\s*(?:verdadero\s*(?:o|/)\s*falso|v\s*(?:o|/)\s*f|true\s*(?:or|/)\s*false|t\s*/\s*f)\s*[:.\-]?\s*"
    r"|^\s*(?:es\s+(?:verdadero|cierto|correcto)\s+que|is\s+it\s+true\s+that)\s+", re.IGNORECASE)
# "... es 15", "... son 15", "... fue 273.40", "... = 22200" (claimed value at the end)
_CLAIM_VERB = re.compile(
    r"\s+(?:es|son|fue|fueron|era|eran|sera|seran|equivale\s+a|es\s+igual\s+a|corresponde\s+a|da|"
    r"resulta\s+en|is|are|was|were|equals|=)\s+(?:de\s+|aproximadamente\s+|igual\s+a\s+|about\s+)?",
    re.IGNORECASE)
# "NVDA es la empresa con ..." (claimed entity at the start)
_CLAIM_HEAD = re.compile(r"^\s*(?P<value>[^\s]+)\s+(?:es|fue|is|was)\s+(?P<rest>(?:la|el|the)\s+.+)$", re.IGNORECASE)
# True/false markers written apart: a "V/F" line, "(V/F)" at the end of a line.
_TF_MARK = r"(?:v\s*/\s*f|v\s+o\s+f|verdadero\s*/\s*falso|verdadero\s+o\s+falso|true\s*/\s*false|t\s*/\s*f)"
_TF_LINE = re.compile(rf"^\s*\(?\s*{_TF_MARK}\s*\)?\s*[.:]?\s*$", re.IGNORECASE)
_TF_INLINE = re.compile(rf"\(\s*{_TF_MARK}\s*\)", re.IGNORECASE)
# Instructions of a true/false item ("Si es falso escribir la respuesta"): not part of the question.
_TF_INSTRUCTION = re.compile(r"^\s*(?:si\s+es\s+falso|si\s+es\s+falsa|if\s+(?:it\s+is\s+)?false)\b", re.IGNORECASE)
# A question starting like this asks for a value, it is not a claim.
_INTERROGATIVE = re.compile(r"^(?:cual(?:es)?|cuant[oa]s?|que|quien(?:es)?|como|donde|cuando|"
                            r"which|what|how|who|when|where)\b")


@dataclass
class ParsedQuestion:
    raw: str
    body: str                     # question text without number, options or claim
    normalized: str               # normalized body used by the interpreter
    kind: str = s.OPEN
    number: int = None
    options: list = field(default_factory=list)   # [(letter, text)]
    claim: str = None


def parse_question(raw):
    text = raw.strip()
    number = None
    m = _NUMBER_PREFIX.match(text)
    if m:
        number, text = int(m.group(1)), text[m.end():]

    text, marked_tf = _strip_tf_markers(text)
    body, options = _split_options(text)
    kind = s.MULTIPLE_CHOICE if len(options) >= 2 else s.OPEN
    if kind == s.OPEN:
        options = []

    claim = None
    if kind == s.OPEN:
        body, claim, explicit = _split_claim(body, marked_tf)
        if claim is not None:
            kind = s.TRUE_FALSE
        elif explicit:
            kind = s.TRUE_FALSE   # marked as T/F but without a value: interpreter reports it
    return ParsedQuestion(raw=raw, body=body.strip(), normalized=normalize(body), kind=kind,
                          number=number, options=options, claim=claim)


def _split_options(text):
    lines = [l for l in text.splitlines() if l.strip()]
    body_lines, options = [], []
    for line in lines:
        m = _OPTION_LINE.match(line)
        if m and (options or len(body_lines) > 0):
            options.append((m.group(1).upper(), m.group(2).strip()))
        elif options:
            # Continuation of the previous option text.
            letter, prev = options[-1]
            options[-1] = (letter, f"{prev} {line.strip()}")
        else:
            body_lines.append(line)
    if len(options) >= 2:
        return "\n".join(body_lines), options

    # Options written on the same line: "... A) 10 B) 12 C) 14"
    joined = " ".join(lines)
    marks = list(_INLINE_OPTION.finditer(joined))
    if len(marks) >= 2:
        options = []
        for i, mk in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(joined)
            options.append((mk.group(1), joined[mk.end():end].strip()))
        return joined[: marks[0].start()], options
    return "\n".join(lines), []


def _strip_tf_markers(text):
    """Removes 'V/F' lines, '(V/F)' marks and 'Si es falso ...' instructions.

    Returns (text, marked) where marked = the item was flagged as true/false.
    """
    marked, lines = False, []
    for line in text.splitlines():
        plain = strip_accents(line)
        if _TF_LINE.match(plain) or _TF_INSTRUCTION.match(plain):
            marked = True
            continue
        cleaned = _TF_INLINE.sub(" ", line)
        marked = marked or cleaned != line
        lines.append(cleaned)
    return "\n".join(lines), marked


def _split_claim(body, marked=False):
    original = body
    text = " ".join(body.split()).lstrip("¿¡ ")
    explicit = marked
    m = _TF_PREFIX.match(strip_accents(text))
    if m:
        explicit = True
        text = text[m.end():]
    # "¿Los registros ... son 15?" is a claim written as a question; "¿Cuantos registros
    # ... son?" is not: without a T/F mark, a question claims only a number and only
    # when it does not start with an interrogative word.
    needs_number = "?" in text and not explicit
    if needs_number and _INTERROGATIVE.match(normalize(text)):
        return original, None, explicit

    # The claimed value follows the LAST verb that leaves a short value at the end.
    for verb in reversed(list(_CLAIM_VERB.finditer(text))):
        value = text[verb.end():].strip().rstrip(".?!¿¡ ").strip()
        if value and _short_value(value) and (not needs_number or any(ch.isdigit() for ch in value)):
            return text[: verb.start()], value, explicit
    head = _CLAIM_HEAD.match(text.rstrip(".?! "))
    if head and not needs_number:
        return head.group("rest"), head.group("value"), explicit
    return (text if explicit else original), None, explicit


def _short_value(value):
    words = value.split()
    has_digit = any(ch.isdigit() for ch in value)
    return len(words) == 1 or (has_digit and len(words) <= 2)
