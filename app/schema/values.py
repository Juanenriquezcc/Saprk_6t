"""Exact search with Spark of a value written in a question ('Ana') in the text columns whose
values are not all in memory (large columns, categories beyond the collected ones).

Only for a capitalized word that nothing else explained, once per word: ONE aggregation per table
(max of the matching value of every column), no rows travel to Python and nothing is logged.
The result is kept for the life of the interpreter (the data of a session does not change).
Normalization mirrors app/text.normalize: lowercase, no accents, the same punctuation removed.
"""
from app.text import normalize

ACCENTS, PLAIN = "áéíóúüñàèìòùâêîôûäëïö", "aeiouunaeiouaeiouaeio"
PUNCTUATION = "[¿?¡!;:\"“”‘’`´(){}\\[\\]'_]"


def _normalized(name):
    from pyspark.sql import functions as F
    col = F.translate(F.lower(F.col("`" + name.replace("`", "``") + "`")), ACCENTS, PLAIN)
    return F.trim(F.regexp_replace(F.regexp_replace(col, PUNCTUATION, " "), r"\s+", " "))


class ValueLookup:
    """lookup('ana maria') -> [(column, stored value)] in the columns not fully indexed."""

    def __init__(self, sources):
        self.sources = sources        # () -> [(DataFrame, DatasetProfile)]
        self.cache = {}
        self.queries = 0              # Spark jobs run (tests and the cost report read it)

    def __call__(self, text):
        text = normalize(text)
        if text not in self.cache:
            self.cache[text] = self._search(text)
        return self.cache[text]

    def _search(self, text):
        from pyspark.sql import functions as F
        found = []
        for df, profile in self.sources():
            columns = [c.name for c in profile.columns
                       if c.kind == "text" and not c.is_date and not c.values_complete and c.name in df.columns]
            if not columns:
                continue
            self.queries += 1
            row = df.agg(*[F.max(F.when(_normalized(c) == F.lit(text), F.col("`" + c.replace("`", "``") + "`")))
                           .alias(f"v{i}") for i, c in enumerate(columns)]).first()
            found += [(c, row[f"v{i}"]) for i, c in enumerate(columns) if row[f"v{i}"] is not None]
        return list(dict.fromkeys(found))
