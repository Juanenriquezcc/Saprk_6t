"""Spanish/English vocabulary of the question interpreter (all regexes work on
normalized text: lowercase, no accents). Mentions of columns are already replaced
by placeholders like @m3@ when these patterns run."""

W = r"(?<![\w@])"      # word start (placeholders count as words)
E = r"(?![\w@])"       # word end

AVG = W + r"(?:promedio|promedia|media|average|avg|mean)" + E
SUM = W + r"(?:suma|sumatoria|total|totales|acumulad[oa]s?|sum)" + E
COUNT = W + r"(?:cuant[oa]s|how\s+many|conteo|contar|count|numero\s+de|cantidad\s+de)" + E
DISTINCT = W + r"(?:distint[oa]s|diferentes|unic[oa]s|distinct|unique)" + E
MAX = W + r"(?:maxim[oa]s?|max|mayor(?:es)?|mas\s+alt[oa]s?|mas\s+grande|highest|largest|maximum|biggest|greatest|top)" + E
MIN = W + r"(?:minim[oa]s?|min|menor(?:es)?|mas\s+baj[oa]s?|mas\s+pequen[oa]s?|lowest|smallest|minimum|least)" + E

ROW_WORDS = r"(?:registros?|filas?|rows?|records?|observaciones|entradas|datos|transacciones|operaciones|veces|dias|days)"
ROWS = W + ROW_WORDS + E
# Count of rows: "cuantos registros", "cantidad de filas", "numero de registros", "total de registros".
COUNT_ROWS_PHRASE = W + r"(?:cantidad|numero|total|conteo)\s+de\s+(?:los\s+)?" + ROW_WORDS + E
MORE_ROWS = W + r"mas\s+" + ROW_WORDS + E                    # "la empresa con mas registros"

RECORD = W + (r"(?:registro|fila|row|record|dia|day|sesion|jornada|cuando|when|individual|un\s+solo|"
              r"una\s+sola|single|transaccion|operacion|registro\s+individual)") + E
PERCENT = W + r"(?:porcentaje|proporcion|percentage|percent|share)" + E + r"|%"
PCT_CHANGE = W + (r"(?:(?:variacion|cambio|rendimiento|retorno|crecimiento)\s+(?:porcentual|en\s+porcentaje|%|relativ[oa])|"
                  r"porcentaje\s+de\s+(?:variacion|cambio)|(?:percent(?:age)?|pct|%)\s+change|return)") + E
PERIOD = W + (r"(?:periodo|period|acumulad[oa]|desde\s+el\s+inicio|del\s+primer\w*\s+al\s+ultimo|"
              r"entre\s+(?:la|el)\s+primer\w*\s+y\s+(?:la|el)\s+ultim\w*|first\s+and\s+last|start\s+to\s+end)") + E
DIFFERENCE = W + r"(?:diferencia|rango|amplitud|spread|difference|range)" + E
COMPARE = W + r"(?:compar\w*|versus|vs)" + E
VERSUS = W + r"(?:respecto\s+(?:a|de)|con\s+respecto\s+a|frente\s+a|relative\s+to|compared\s+to|vs|versus)" + E

# "por empresa", "por cada empresa", "de cada empresa", "by company", "per company"
GROUP_BY = W + r"(?:por\s+cada|por|de\s+cada|para\s+cada|en\s+cada|by|per|for\s+each|each)\s+(?:la\s+|el\s+|the\s+)?@m(\d+)@"
# "cual empresa", "que empresa", "cual es la empresa", "which company"
WHICH = (W + r"(?:cual(?:es)?|que|which|what|quien)(?:\s+(?:es|fue|son|fueron|is|was|are|were))?"
         r"(?:\s+(?:la|el|las|los|the))?\s+@m(\d+)@")
# "la empresa con mayor ...", "the company with the highest ..."
ENTITY_WITH = r"@m(\d+)@\s+(?:con|que\s+tuvo|que\s+tiene|que\s+presento|que\s+registro|with|that\s+had|having)" + E
TOP_N = W + r"(?:top|las|los|primer[oa]s|first)\s+(\d{1,2})\s+(?:@m\d+@|" + ROW_WORDS + ")"

# Comparison operators, longest first. Value = SQL operator.
# "al" is the Spanish contraction a + el ("superior al precio de apertura").
OPERATORS = [
    (r">=|=>", ">="), (r"<=|=<", "<="), (r"!=|<>", "!="), (r">", ">"), (r"<", "<"), (r"==|=", "="),
    (r"mayor\s+o\s+igual\s+(?:que|al?)", ">="), (r"menor\s+o\s+igual\s+(?:que|al?)", "<="),
    (r"greater\s+than\s+or\s+equal\s+to", ">="), (r"less\s+than\s+or\s+equal\s+to", "<="),
    (r"al\s+menos|como\s+minimo|at\s+least", ">="), (r"como\s+maximo|a\s+lo\s+sumo|at\s+most", "<="),
    (r"mayor\s+(?:que|al?)", ">"), (r"menor\s+(?:que|al?)", "<"), (r"superior(?:es)?\s+al?", ">"),
    (r"inferior(?:es)?\s+al?", "<"), (r"supera(?:n|ba|ron)?(?:\s+al?)?", ">"), (r"exced(?:e|en|io)(?:\s+al?)?", ">"),
    (r"por\s+encima\s+(?:de|del)", ">"), (r"por\s+debajo\s+(?:de|del)", "<"),
    (r"mas\s+de", ">"), (r"menos\s+de", "<"), (r"greater\s+than|more\s+than|above|over", ">"),
    (r"less\s+than|fewer\s+than|below|under", "<"), (r"igual\s+(?:al?|que)|equal\s+to", "="),
    (r"distint[oa]\s+(?:de|del|al?)|diferente\s+(?:de|del|al?)|not\s+equal\s+to", "!="),
]
COPULA = r"(?:\s+(?:es|son|sea|sean|fue|fueron|fuera|fuese|era|eran|esta|estuvo|estuviera|was|were|is|are|be|been))?"
ARTICLES = r"(?:\s+(?:el|la|los|las|lo|al|su|sus|the|a|an|de|del|valor|precio))*"

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# Generic words that may refer to several numeric roles (always resolved with care).
GENERIC_WORDS = {
    "precio": ("PRICE",), "precios": ("PRICE",), "price": ("PRICE",), "prices": ("PRICE",),
    "valor": ("PRICE", "AMOUNT", "TOTAL"), "valores": ("PRICE", "AMOUNT", "TOTAL"), "value": ("PRICE", "AMOUNT", "TOTAL"),
    "monto": ("AMOUNT", "TOTAL"), "importe": ("AMOUNT", "TOTAL"), "amount": ("AMOUNT", "TOTAL"),
    "cotizacion": ("PRICE",),
}
# Role aliases containing these words ('precio maximo') are ambiguous in questions: the
# word may be the aggregation. They are read as the role (HIGH/LOW) only when another
# operation word remains in the question; otherwise the question is scanned without them.
OPERATION_WORDS = {"max", "min", "maximo", "minimo", "alto", "bajo", "total"}
OP_ALIAS_HEADS = {"precio", "price"}        # only 'precio maximo', 'price high'... qualify
OPERATION_KEYWORDS = [AVG, SUM, COUNT, MAX, MIN, DIFFERENCE, PCT_CHANGE, PERCENT]
# Role aliases that are also operation words: never read as a column mention in questions.
ROLE_WORDS_EXCLUDED = {"max", "min", "maximo", "minimo", "alto", "bajo", "total", "valor", "precio", "price",
                       "monto", "importe", "amount", "type", "time", "key", "code", "group", "stock", "accion"}
