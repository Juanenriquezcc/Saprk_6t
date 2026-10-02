"""Text helpers shared by the semantic detector and the question interpreter."""
import re
import unicodedata

# Short words that must never be read as a column name, a value or a role.
STOPWORDS = {
    "a", "al", "de", "del", "el", "la", "las", "lo", "los", "en", "y", "o", "u", "e", "con", "por",
    "para", "que", "se", "un", "una", "su", "sus", "es", "son", "the", "of", "to", "in", "and", "or",
    "is", "by", "on", "at", "an", "as",
}


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text):
    """Lowercase, no accents, punctuation removed except what numbers, dates and
    comparison operators need (digits with . , - / %, and < > = !)."""
    text = strip_accents(text).lower()
    text = re.sub(r"[¿?¡!;:\"“”‘’`´(){}\[\]']", " ", text)
    text = re.sub(r"(?<!\d),|,(?!\d)", " ", text)     # keep decimal commas only
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)   # keep decimal points only
    return re.sub(r"\s+", " ", text).strip()


def name_tokens(name):
    """'PrecioCierre' / 'precio_cierre' / 'Precio Cierre' -> ['precio', 'cierre']."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [t for t in re.split(r"[^a-z0-9]+", strip_accents(spaced).lower()) if t]


def phrase_pattern(words):
    """Regex for a word sequence that tolerates connectors ('precio de cierre') and
    simple plurals on the last word ('empresas')."""
    connector = r"(?:\s+(?:de|del|of|la|el))?\s+"
    body = connector.join(re.escape(w) for w in words)
    return rf"(?<![\w@]){body}(?:s|es)?(?![\w@])"
