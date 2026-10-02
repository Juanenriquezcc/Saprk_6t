"""Golden tests: the 10 REAL questions of the lab reference, word for word.

The same questions run on two datasets with different column names, order, separator
and language (stocks_small.csv / acciones_es.csv). Expected values come from an
independent oracle (Python csv module over the small fixtures), never from the engine,
and none of these questions or columns exists inside the engine.

The document's option values belong to the real lab dataset, so multiple-choice options
here are built from the oracle (correct value plus close distractors).
"""
import csv
from collections import defaultdict

import pytest

from app.query import spec as s
from app.query.builder import ident
from conftest import DATA, chooser

DATASETS = {
    # file: (separator, column for each concept)
    "stocks_small.csv": (",", dict(date="Date", company="Company", open="Open", high="High", low="Low",
                                   close="Close", volume="Volume")),
    "acciones_es.csv": (";", dict(date="fecha", company="empresa", open="precio_apertura", high="precio_maximo",
                                  low="precio_minimo", close="precio_cierre", volume="volumen_negociado")),
}

Q = {
    1: "¿Cuántos registros contiene el dataset?",
    2: "¿Cuál es el precio promedio de cierre (Close) considerando todas las acciones?",
    3: "¿Cuál fue el precio de cierre máximo registrado?",
    4: "¿Cuál empresa presentó el mayor volumen total negociado?",
    5: "¿Cuál fue el volumen total negociado considerando todas las empresas?",
    6: ("¿Los registros corresponden a días en los que el precio de cierre fue\n"
        "superior al precio de apertura son {claim}?\nV/F\nSi es falso escribir la respuesta."),
    7: "¿Cuál empresa presentó el mayor precio promedio de cierre?",
    8: ("¿El mayor rango diario registrado? Recuerde:\nRango = High - Low es {claim}.\n"
        "V/F\nSi es falso escribir la respuesta."),
    9: "¿Cuál empresa presentó el mayor promedio de variación porcentual\nentre apertura y cierre?",
    10: "¿Cuál fue el registro con mayor volumen individual?",
}
INTENTS = {1: s.COUNT_ROWS, 2: s.AGG_SCALAR, 3: s.AGG_SCALAR, 4: s.GROUP_TOP, 5: s.AGG_SCALAR,
           6: s.COUNT_WHERE, 7: s.GROUP_TOP, 8: s.DIFFERENCE, 9: s.PCT_CHANGE, 10: s.RECORD_EXTREME}
DOCUMENT_CLAIMS = {6: 15, 8: 9}


# --- independent oracle ---------------------------------------------------------

def oracle(name):
    sep, cols = DATASETS[name]
    with open(DATA / name, encoding="utf-8") as f:
        rows = [{k: (r[c] if k in ("date", "company") else float(r[c])) for k, c in cols.items()}
                for r in csv.DictReader(f, delimiter=sep)]
    by_company = defaultdict(list)
    for r in rows:
        by_company[r["company"]].append(r)

    def best(metric):
        values = {c: metric(rs) for c, rs in by_company.items()}
        top = max(values, key=values.get)
        return top, values[top]

    pct = lambda r: (r["close"] - r["open"]) / r["open"] * 100            # noqa: E731
    biggest = max(rows, key=lambda r: r["volume"])
    return {
        1: len(rows),
        2: sum(r["close"] for r in rows) / len(rows),
        3: max(r["close"] for r in rows),
        4: best(lambda rs: sum(r["volume"] for r in rs)),
        5: sum(r["volume"] for r in rows),
        6: sum(1 for r in rows if r["close"] > r["open"]),
        7: best(lambda rs: sum(r["close"] for r in rs) / len(rs)),
        8: max(r["high"] - r["low"] for r in rows),
        9: best(lambda rs: sum(pct(r) for r in rs) / len(rs)),
        10: (biggest["company"], biggest["date"], biggest["volume"]),
        "companies": sorted(by_company),
    }


def question(n, claim=None):
    text = Q[n].format(claim=claim if claim is not None else DOCUMENT_CLAIMS.get(n))
    return f"{n}. {text}"


def expected_sql(n, c):
    c = {k: ident(v) for k, v in c.items()}
    return {
        1: ["SELECT COUNT(*) AS total_registros\nFROM dataset"],
        2: [f"AVG({c['close']})"],
        3: [f"MAX({c['close']})"],
        4: [f"SUM({c['volume']})", f"GROUP BY {c['company']}", "DESC NULLS LAST", "LIMIT 1"],
        5: [f"SUM({c['volume']})"],
        6: ["COUNT(*)", f"WHERE {c['close']} > {c['open']}"],
        7: [f"AVG({c['close']})", f"GROUP BY {c['company']}", "DESC NULLS LAST", "LIMIT 1"],
        8: [f"MAX(({c['high']} - {c['low']}))"],
        9: [f"AVG((({c['close']} - {c['open']}) / NULLIF({c['open']}, 0) * 100))",
            f"GROUP BY {c['company']}", "DESC NULLS LAST", "LIMIT 1"],
        10: ["SELECT *", f"ORDER BY {c['volume']} DESC", "LIMIT 1"],
    }[n]


# --- open answers, evidence and SQL ------------------------------------------------

@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("n", range(1, 11))
def test_real_question(lab, dataset, n):
    o = oracle(dataset)[n]
    ev = lab(dataset).ask(question(n), chooser({}))

    # evidence
    assert ev.number == n and ev.intent == INTENTS[n]
    assert ev.sql and ev.interpretation.startswith(INTENTS[n]) and ev.timestamp and ev.result_rows
    assert ev.spec["intent"] == INTENTS[n]
    for fragment in expected_sql(n, DATASETS[dataset][1]):
        assert fragment in ev.sql, (fragment, ev.sql)
    assert "max_by" not in ev.sql          # Q9 is NOT the first-vs-last period change

    # result
    if n in (4, 7, 9):                     # company + value
        company, value = o
        assert ev.value == company
        assert ev.result_rows[0][1] == pytest.approx(value)
    elif n == 10:                          # whole record, with company and date
        company, date, volume = o
        row = dict(zip(ev.result_columns, ev.result_rows[0]))
        cols = DATASETS[dataset][1]
        assert (row[cols["company"]], row[cols["date"]][:10], row[cols["volume"]]) == (company, date, volume)
        assert ev.value == volume
        assert company in ev.result_text and date in ev.result_text   # the record is shown, not only the value
    elif n in DOCUMENT_CLAIMS:             # V/F with the document's claimed value
        assert ev.question_type == s.TRUE_FALSE and ev.claim == str(DOCUMENT_CLAIMS[n])
        assert ev.value == pytest.approx(o)
        assert ev.verdict == ("VERDADERO" if o == DOCUMENT_CLAIMS[n] else "FALSO")
    else:
        assert ev.value == pytest.approx(o)


# --- true / false ------------------------------------------------------------------

@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("n", [6, 8])
def test_real_true_false(lab, dataset, n):
    o = oracle(dataset)[n]
    session = lab(dataset)
    wrong = session.ask(question(n, claim=int(o) + 7), chooser({}))
    assert wrong.verdict == "FALSO" and wrong.answer == "FALSO"
    assert wrong.correct_value == f"{o:g}"            # "si es falso escribir la respuesta"
    right = session.ask(question(n, claim=f"{o:g}"), chooser({}))
    assert right.verdict == "VERDADERO"


# --- multiple choice -----------------------------------------------------------------

def numeric_options(value, decimals):
    fmt = f"{{:.{decimals}f}}"
    return [fmt.format(v) for v in (value * 0.8, value, value * 1.15, value + 25)]


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 9, 10])
def test_real_multiple_choice(lab, dataset, n):
    o = oracle(dataset)
    if n in (4, 7, 9):
        texts = o["companies"] + ["TSLA"]
        correct = texts.index(o[n][0])
    elif n == 10:
        company, date, _ = o[n]
        other = next(c for c in o["companies"] if c != company)
        day = int(date[-2:])
        texts = [f"{company} - {date[:-2]}{day - 1:02d}", f"{other} - {date}", f"{company} - {date}",
                 f"{other} - {date[:-2]}{day - 1:02d}"]
        correct = 2
    else:
        texts = numeric_options(o[n], 0 if n in (1, 5) else 2)
        correct = 1
    letters = "ABCD"
    block = question(n) + "\n" + "\n".join(f"{letters[i]}) {t}" for i, t in enumerate(texts))
    ev = lab(dataset).ask(block, chooser({}))
    assert ev.question_type == s.MULTIPLE_CHOICE
    assert ev.matched_option == letters[correct], (ev.answer, ev.value, texts)
    assert ev.answer == f"{letters[correct]}) {texts[correct]}"


def test_no_option_matches_is_reported(lab):
    ev = lab("stocks_small.csv").ask(question(1) + "\nA) 10\nB) 11\nC) 13\nD) 14", chooser({}))
    assert ev.matched_option is None and ev.answer == "Ninguna opcion coincide con el resultado calculado."
