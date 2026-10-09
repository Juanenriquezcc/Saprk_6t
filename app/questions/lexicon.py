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
MEDIAN = W + r"(?:mediana|median)" + E
PERCENTILE = W + r"(?:percentil|percentile|p)\s*(\d{1,2})" + E
STDDEV = W + r"(?:desviacion(?:\s+(?:estandar|tipica))?|std|stddev|standard\s+deviation)" + E
VARIANCE = W + r"(?:varianza|variance)" + E

ROW_WORDS = (r"(?:registros?|filas?|rows?|records?|observaciones|entradas|datos|transacciones|operaciones|veces|dias|days|"
             r"pedidos?|ordenes|orders?|compras|envios|facturas)")
ROWS = W + ROW_WORDS + E
# Count of rows: "cuantos registros", "cantidad de filas", "numero de registros", "total de registros".
COUNT_ROWS_PHRASE = W + r"(?:cantidad|numero|total|conteo)\s+de\s+(?:los\s+)?" + ROW_WORDS + E
COUNT_NOUNS = {"cantidad", "numero", "conteo", "total"}    # 'cantidad total de pedidos' still counts
MORE_ROWS = W + r"mas\s+" + ROW_WORDS + E                    # "la empresa con mas registros"
MORE_METRIC = W + r"(?:mas|more|most)\s+(?:de\s+)?@m\d+@"           # "mas unidades" (a numeric mention)
LESS_METRIC = W + r"(?:menos|less|fewer|least)\s+(?:de\s+)?@m\d+@"
# "mayor cantidad", "menor numero de unidades", "highest quantity": a superlative right before a
# measure. Read as a TOTAL only when the measure is a count of units (UNIT_ROLES); "mayor precio"
# or a bare "mayor volumen" stay ambiguous.
SUPERLATIVE_METRIC = rf"(?:{MAX}|{MIN})\s+(?:de\s+|of\s+)?(?:la\s+|el\s+|the\s+)?@m\d+@"
UNIT_ROLES = {"QUANTITY", "RETURNS"}
# "cuantas unidades": how many UNITS (a total of the measure), when the measure counts units.
COUNT_UNITS = W + r"(?:cuant[oa]s|how\s+many)\s+(@m\d+@)"

# Ordinary words of questions: never read as a value ('Total' is not a client). The words of every
# pattern of this module are added at run time (intents._vocabulary).
QUESTION_WORDS = {
    "cual", "cuales", "cuanto", "cuanta", "cuantos", "cuantas", "que", "quien", "quienes", "como", "donde",
    "cuando", "hizo", "hicieron", "compro", "compraron", "vendio", "vendieron", "tiene", "tienen", "tuvo",
    "tuvieron", "hay", "hubo", "fue", "fueron", "es", "son", "esta", "estan", "registro", "registros",
    "pedido", "pedidos", "cliente", "clientes", "producto", "productos", "dato", "datos", "valor", "valores",
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo", "verdadero", "falso",
}

# Questions over several catalog tables (app/questions/joins.py). Table names are added at run time.
# "... incluyendo los que no tienen pedidos": keep the entities without related rows (LEFT JOIN).
KEEP_UNMATCHED = r"(?:,\s*)?" + W + r"(?:incluyendo|incluidos?|incluidas?|incluso|aunque|including)" + E + r".*$"
NEGATION = W + r"(?:no|sin|ningun[oa]?|without|none)" + E
# "clientes que no tienen pedidos" / "clientes sin pedidos" (LEFT ANTI JOIN), between two table words.
ANTI_LINK = (r"\s+(?:que\s+)?(?:no\s+(?:tienen|tiene|tuvieron|tuvo|registran|registra|hicieron|hizo|realizaron|"
             r"realizo|aparecen|aparece|han\s+\w+|ha\s+\w+)|sin|without|with\s+no)\s+(?:ningun[oa]?\s+|any\s+)?")
# "cuantos pedidos", "numero de pedidos", "mas pedidos": the table whose rows are counted.
COUNT_HEAD = W + (r"(?:cuant[oa]s|numero\s+de|cantidad\s+de|conteo\s+de|total\s+de|mas|menos|how\s+many|"
                  r"number\s+of)\s+(?:los\s+|las\s+)?")

RECORD = W + (r"(?:registro|fila|row|record|dia|day|sesion|jornada|cuando|when|individual|un\s+solo|"
              r"una\s+sola|single|transaccion|operacion|registro\s+individual|"
              r"pedido|orden|factura|compra|envio|order|invoice|purchase)") + E   # singular: "el pedido con mas ..."
PERCENT = W + r"(?:porcentaje|proporcion|percentage|percent|share)" + E + r"|%"
PCT_CHANGE = W + (r"(?:(?:variacion|cambio|rendimiento|retorno|crecimiento)\s+(?:porcentual|en\s+porcentaje|%|relativ[oa])|"
                  r"porcentaje\s+de\s+(?:variacion|cambio)|(?:percent(?:age)?|pct|%)\s+change|return)") + E
PERIOD = W + (r"(?:periodo|period|acumulad[oa]|desde\s+el\s+inicio|del\s+primer\w*\s+al\s+ultimo|"
              r"entre\s+(?:la|el)\s+primer\w*\s+y\s+(?:la|el)\s+ultim\w*|first\s+and\s+last|start\s+to\s+end)") + E
DIFFERENCE = W + r"(?:diferencia|rango|amplitud|spread|difference|range)" + E
COMPARE = W + r"(?:compar\w*|versus|vs)" + E
VERSUS = W + r"(?:respecto\s+(?:a|de)|con\s+respecto\s+a|frente\s+a|relative\s+to|compared\s+to|vs|versus)" + E

# "por empresa", "por cada empresa", "de cada empresa", "hizo cada cliente", "by company", "per company"
GROUP_BY = W + r"(?:por\s+cada|por|de\s+cada|para\s+cada|en\s+cada|cada|by|per|for\s+each|each)\s+(?:la\s+|el\s+|the\s+)?@m(\d+)@"
# "por vendedor" / "cada cliente" where the word is NOT a column: a grouping that must never be
# dropped (F1). Checked only when no grouping was found. Words that do not group are excluded.
GROUP_WORD = (W + r"(?:por\s+cada|por|de\s+cada|para\s+cada|en\s+cada|cada|by|per|for\s+each|each)"
              r"\s+(?:la\s+|el\s+|los\s+|las\s+|the\s+)?(?P<w>[a-z][a-z0-9]*)" + E)
NOT_A_GROUP = {"ciento", "favor", "ejemplo", "lo", "encima", "debajo", "separado", "tanto", "ultimo", "otro",
               "otra", "uno", "una", "vez", "veces", "medio", "cierto", "supuesto", "fin", "completo", "igual"}
# "cual empresa", "que empresa", "cual es la empresa", "which company"
WHICH = (W + r"(?:cual(?:es)?|que|which|what|quien)(?:\s+(?:es|fue|son|fueron|is|was|are|were))?"
         r"(?:\s+(?:la|el|las|los|the))?\s+@m(\d+)@")
# "la empresa con mayor ...", "the company with the highest ..."
ENTITY_WITH = r"@m(\d+)@\s+(?:con|que\s+tuvo|que\s+tiene|que\s+presento|que\s+registro|with|that\s+had|having)" + E
TOP_N = W + r"(?:top|las|los|primer[oa]s|first)\s+(\d{1,2})\s+(?:@m\d+@|" + ROW_WORDS + ")"

# Time grains: "por mes", "en que ano", "mensual", "trimestre". 'dia' only with "por"/"diario":
# "que dia ..." asks for the date of a record, not for a grouping.
TIME_GROUP = [
    (W + r"(?:por|cada|por\s+cada|de\s+cada|para\s+cada|en\s+cada)\s+(?:el\s+|la\s+)?(mes|ano|anio|trimestre|dia)(?:s|es)?" + E, "group"),
    (W + r"(?:en\s+que|en\s+cual|que|cual)(?:\s+(?:fue|es))?(?:\s+(?:el|la))?\s+(mes|ano|anio|trimestre)" + E, "which"),
    (W + r"(mensual|anual|trimestral)(?:es|mente)?" + E, "group"),   # not "diario": "rango diario" is per row
]
TIME_WORDS = {"mes": "month", "mensual": "month", "ano": "year", "anio": "year", "anual": "year",
              "trimestre": "quarter", "trimestral": "quarter", "dia": "day"}
CUMULATIVE = W + r"(?:acumulad[oa]s?|acumulativ[oa]|running\s+total|cumulative)" + E
# HAVING: "con mas de 100 pedidos" (count of rows per group)
HAVING_COUNT = (W + r"(?:con|tienen|tiene|tuvieron|tuvo|registran|registra|que\s+tienen|que\s+tuvieron)?\s*"
                r"(mas\s+de|menos\s+de|al\s+menos|como\s+minimo|como\s+maximo)\s+(\d[\d.,]*)\s+" + ROW_WORDS + E)
HAVING_COUNT_OPS = {"mas de": ">", "menos de": "<", "al menos": ">=", "como minimo": ">=", "como maximo": "<="}
# HAVING on an aggregate: "con un promedio de cierre mayor a 100" / "cuyo cierre promedio supera 100"
HAVING_AGG_WORDS = {"promedio": "AVG", "media": "AVG", "suma": "SUM", "total": "SUM", "maximo": "MAX", "minimo": "MIN"}
# Comparative true/false claim: "... es mayor a 500000"
CLAIM_COMPARATIVE = [
    (r"(?:mayor|superior|mas)\s+(?:a|que|al|de)", ">"), (r"(?:menor|inferior|menos)\s+(?:a|que|al|de)", "<"),
    (r"(?:al\s+menos|como\s+minimo|mayor\s+o\s+igual\s+(?:a|que))", ">="),
    (r"(?:como\s+maximo|a\s+lo\s+sumo|menor\s+o\s+igual\s+(?:a|que))", "<="),
]

# Comparison operators, longest first. Value = SQL operator. Plurals too: "unidades mayores a 1".
# "al" is the Spanish contraction a + el ("superior al precio de apertura").
OPERATORS = [
    (r">=|=>", ">="), (r"<=|=<", "<="), (r"!=|<>", "!="), (r">", ">"), (r"<", "<"), (r"==|=", "="),
    (r"mayor(?:es)?\s+o\s+igual(?:es)?\s+(?:que|al?)", ">="), (r"menor(?:es)?\s+o\s+igual(?:es)?\s+(?:que|al?)", "<="),
    (r"greater\s+than\s+or\s+equal\s+to", ">="), (r"less\s+than\s+or\s+equal\s+to", "<="),
    (r"al\s+menos|como\s+minimo|at\s+least", ">="), (r"como\s+maximo|a\s+lo\s+sumo|at\s+most", "<="),
    (r"mayor(?:es)?\s+(?:que|al?)", ">"), (r"menor(?:es)?\s+(?:que|al?)", "<"), (r"superior(?:es)?\s+al?", ">"),
    (r"inferior(?:es)?\s+al?", "<"), (r"supera(?:n|ba|ron)?(?:\s+al?)?", ">"), (r"exced(?:e|en|io)(?:\s+al?)?", ">"),
    (r"por\s+encima\s+(?:de|del)", ">"), (r"por\s+debajo\s+(?:de|del)", "<"),
    (r"mas\s+de", ">"), (r"menos\s+de", "<"), (r"greater\s+than|more\s+than|above|over", ">"),
    (r"less\s+than|fewer\s+than|below|under", "<"), (r"igual\s+(?:al?|que)|equal\s+to", "="),
    (r"distint[oa]\s+(?:de|del|al?)|diferente\s+(?:de|del|al?)|not\s+equal\s+to", "!="),
]
# Numbers written as words, read ONLY as the value of a comparison ("stock mayor que cero").
# 'uno' / 'una' are left out: after an operator they are usually an article or a pronoun.
NUMBER_WORDS = {"cero": 0, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
                "nueve": 9, "diez": 10, "zero": 0}
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
    # The value of a SALE (an amount per order) is not the unit price, and 'valor unitario' is a price.
    # Own phrases, so a choice remembered for a bare 'valor' never decides them (longest match wins).
    "valor de venta": ("AMOUNT", "TOTAL"), "valor de la venta": ("AMOUNT", "TOTAL"),
    "valor de cada venta": ("AMOUNT", "TOTAL"), "valor venta": ("AMOUNT", "TOTAL"),
    "importe de venta": ("AMOUNT", "TOTAL"), "importe de la venta": ("AMOUNT", "TOTAL"),
    "monto de venta": ("AMOUNT", "TOTAL"), "monto de la venta": ("AMOUNT", "TOTAL"),
    "valor unitario": ("PRICE",), "valor por unidad": ("PRICE",),
    # Revenue concept: an existing column or a formula (quantity x price ...). Always asked
    # when there is more than one possibility.
    "ingresos": ("AMOUNT", "TOTAL"), "ingreso": ("AMOUNT", "TOTAL"), "revenue": ("AMOUNT", "TOTAL"),
    "facturacion": ("AMOUNT", "TOTAL"), "ventas": ("AMOUNT", "TOTAL"), "sales": ("AMOUNT", "TOTAL"),
    "recaudo": ("AMOUNT", "TOTAL"), "valor vendido": ("AMOUNT", "TOTAL"),
}
REVENUE_WORDS = {"ingresos", "ingreso", "revenue", "facturacion", "ventas", "sales", "recaudo", "valor vendido"}
COUNTABLE_WORDS = {"ventas", "sales"}     # "mas ventas" may also mean "more sales rows"
# Role aliases containing these words ('precio maximo') are ambiguous in questions: the
# word may be the aggregation. They are read as the role (HIGH/LOW) only when another
# operation word remains in the question; otherwise the question is scanned without them.
OPERATION_WORDS = {"max", "min", "maximo", "minimo", "alto", "bajo", "total"}
OP_ALIAS_HEADS = {"precio", "price"}        # only 'precio maximo', 'price high'... qualify
OPERATION_KEYWORDS = [AVG, SUM, COUNT, MAX, MIN, DIFFERENCE, PCT_CHANGE, PERCENT, MEDIAN, STDDEV, VARIANCE]
# Role aliases that are also operation words: never read as a column mention in questions.
ROLE_WORDS_EXCLUDED = {"max", "min", "maximo", "minimo", "alto", "bajo", "total", "valor", "precio", "price",
                       "monto", "importe", "amount", "type", "time", "key", "code", "group", "stock", "accion",
                       "ingreso", "ingresos", "revenue", "venta", "ventas", "sales", "desc", "pago"}
