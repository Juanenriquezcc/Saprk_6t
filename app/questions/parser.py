"""Splits a raw lab question into: number, body, options (A-E) and claim (true/false)."""
import re
from dataclasses import dataclass, field

from app.query import spec as s
from app.questions import lexicon as lx
from app.text import normalize, strip_accents

_NUMBER_PREFIX = re.compile(r"^\s*(?:p|pregunta|question|q)?\s*(\d{1,3})\s*[.:)\-]\s+", re.IGNORECASE)
# 'A. Monitor 24', 'B) Smartphone X', '+D. Laptop Pro 14' (a leading + * or check marks the option
# the MATERIAL gives as correct; it is not part of the text), 'D. Laptop Pro 14 [correcta]'.
_OPTION_LINE = re.compile(r"^\s*(?P<mark>[+*✓✔]\s*)?\(?(?P<letter>[A-Ea-e])\s*[).:\-]\s*(?P<text>.+?)\s*$")
_OPTION_TAIL_MARK = re.compile(r"\s*(?:\[\s*correct[ao]\s*\]|\(\s*correct[ao]\s*\)|[✓✔])\s*$", re.IGNORECASE)
_INLINE_OPTION = re.compile(r"(?:^|\s)(?P<mark>[+*]\s*)?\(?([A-E])\)\s*")
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
_TF_INLINE = re.compile(rf"\(\s*{_TF_MARK}\s*\)|(?<=[\s?.])\s*{_TF_MARK}\s*[.:]?\s*$", re.IGNORECASE)
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
    claim_op: str = "="           # '... es mayor a 100' -> '>'
    marked_option: str = None     # letter the MATERIAL marks as correct ('+D.'), never the student's answer
    options_issue: str = None     # the options cannot be read safely (repeated letters...)


def parse_question(raw):
    text = raw.strip()
    number = None
    m = _NUMBER_PREFIX.match(text)
    if m:
        number, text = int(m.group(1)), text[m.end():]

    text, marked_tf = _strip_tf_markers(text)
    body, options, marked_option = _split_options(text)
    letters = [letter for letter, _ in options]
    issue = (f"letras de opcion repetidas ({', '.join(sorted({l for l in letters if letters.count(l) > 1}))})"
             if len(set(letters)) != len(letters) else None)
    kind = s.MULTIPLE_CHOICE if len(options) >= 2 else s.OPEN
    if kind == s.OPEN:
        options = []

    claim, claim_op = None, "="
    if kind == s.OPEN:
        body, claim, explicit, claim_op = _split_claim(body, marked_tf)
        if claim is not None:
            kind = s.TRUE_FALSE
        elif explicit:
            kind = s.TRUE_FALSE   # marked as T/F but without a value: interpreter reports it
    return ParsedQuestion(raw=raw, body=body.strip(), normalized=normalize(body), kind=kind,
                          number=number, options=options, claim=claim, claim_op=claim_op,
                          marked_option=marked_option if kind == s.MULTIPLE_CHOICE else None, options_issue=issue)


def _clean_option(text):
    """(text without a trailing '[correcta]' / check mark, marked)."""
    cleaned = _OPTION_TAIL_MARK.sub("", text).strip()
    return cleaned, cleaned != text.strip()


def _split_options(text):
    """(body, [(letter, text)], letter marked as correct by the material or None)."""
    lines = [l for l in text.splitlines() if l.strip()]
    body_lines, options, marked = [], [], None
    for line in lines:
        m = _OPTION_LINE.match(line)
        if m and (options or len(body_lines) > 0):
            letter = m.group("letter").upper()
            option, tail_mark = _clean_option(m.group("text"))
            options.append((letter, option))
            if m.group("mark") or tail_mark:
                marked = letter
        elif options:
            # Continuation of the previous option text.
            letter, prev = options[-1]
            options[-1] = (letter, f"{prev} {line.strip()}")
        else:
            body_lines.append(line)
    if len(options) >= 2:
        return "\n".join(body_lines), options, marked

    # Options written on the same line: "... A) 10 B) 12 +C) 14"
    joined = " ".join(lines)
    marks = list(_INLINE_OPTION.finditer(joined))
    if len(marks) >= 2:
        options, marked = [], None
        for i, mk in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(joined)
            option, tail_mark = _clean_option(joined[mk.end():end])
            options.append((mk.group(2), option))
            if mk.group("mark") or tail_mark:
                marked = mk.group(2)
        return joined[: marks[0].start()], options, marked
    return "\n".join(lines), [], None


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
        return original, None, explicit, "="

    # The claimed value follows the LAST verb that leaves a short value at the end.
    for verb in reversed(list(_CLAIM_VERB.finditer(text))):
        value = text[verb.end():].strip().rstrip(".?!¿¡ ").strip()
        compared = _COMPARATIVE.fullmatch(strip_accents(value).lower())
        if compared:
            op = next(sql for rx, sql in lx.CLAIM_COMPARATIVE if re.fullmatch(rx, compared.group("op")))
            return text[: verb.start()], value[compared.start("num"):].strip(), explicit, op
        if value and _short_value(value) and (not needs_number or any(ch.isdigit() for ch in value)):
            return text[: verb.start()], value, explicit, "="
    head = _CLAIM_HEAD.match(text.rstrip(".?! "))
    if head and not needs_number:
        return head.group("rest"), head.group("value"), explicit, "="
    return (text if explicit else original), None, explicit, "="


_COMPARATIVE = re.compile(
    r"(?P<op>" + "|".join(rx for rx, _ in lx.CLAIM_COMPARATIVE) + r")\s+(?:los\s+|las\s+|el\s+|la\s+)?"
    r"(?P<num>[-+]?\$?\s?\d[\d.,]*%?)")


def _short_value(value):
    words = value.split()
    has_digit = any(ch.isdigit() for ch in value)
    return len(words) == 1 or (has_digit and len(words) <= 2)
