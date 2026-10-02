"""Profiling and semantic role detection on unknown datasets."""
from app.schema.profiler import _text_date_format


def test_profile_stocks(lab):
    p = lab("stocks_small.csv").profile
    assert p.rows == 12 and len(p.columns) == 7
    company = p.column("Company")
    assert company.kind == "text" and company.is_categorical and company.values == ["AAPL", "MSFT", "NVDA"]
    close = p.column("Close")
    assert close.kind == "numeric" and close.min == 52.0 and close.max == 318.0
    assert abs(close.mean - 154.0416667) < 1e-6
    assert p.column("Date").is_date and p.column("Date").min.isoformat() == "2024-01-02"
    assert [c.name for c in p.of_kind("numeric")] == ["Open", "High", "Low", "Close", "Volume"]
    assert len(p.samples) == 5
    assert all(c.nulls == 0 for c in p.columns)


def test_profile_ids_and_categories(lab):
    p = lab("ventas.csv").profile
    assert p.column("id_venta").is_id
    assert [c.name for c in p.categorical] == ["producto", "categoria"]
    assert p.column("categoria").values == ["Bebidas", "Panaderia"]


def test_text_date_formats():
    assert _text_date_format(["2024-01-02", "2024-12-31"]) == "yyyy-MM-dd"
    assert _text_date_format(["02/01/2024", "31/12/2024"]) == "dd/MM/yyyy"
    assert _text_date_format(["01/31/2024"]) == "MM/dd/yyyy"
    assert _text_date_format(["hola", "2024-01-02"]) is None


def test_roles_with_english_names(lab):
    sem = lab("stocks_small.csv").semantic
    for role, column in [("DATE", "Date"), ("OPEN", "Open"), ("CLOSE", "Close"), ("HIGH", "High"),
                         ("LOW", "Low"), ("VOLUME", "Volume"), ("COMPANY", "Company")]:
        assert sem.resolved(role) == column, role
    # "price" fits four columns equally: never guessed.
    assert sem.status("PRICE") == "ambiguous"
    assert {c.column for c in sem.tied("PRICE")} == {"Open", "High", "Low", "Close"}


def test_roles_with_spanish_names_in_other_order(lab):
    sem = lab("acciones_es.csv").semantic
    assert sem.resolved("CLOSE") == "precio_cierre"
    assert sem.resolved("OPEN") == "precio_apertura"
    assert sem.resolved("HIGH") == "precio_maximo"
    assert sem.resolved("LOW") == "precio_minimo"
    assert sem.resolved("VOLUME") == "volumen_negociado"
    assert sem.resolved("COMPANY") == "empresa"
    assert sem.resolved("DATE") == "fecha"


def test_ambiguous_price_columns_are_reported(lab):
    sem = lab("ventas.csv").semantic
    # Both are price candidates (the generic word "precio" asks between them: see test_question_engine).
    assert [c.column for c in sem.of("PRICE")] == ["precio_unitario", "precio_total"]
    assert sem.resolved("QUANTITY") == "cantidad"
    assert sem.resolved("PRODUCT") == "producto"
    assert sem.resolved("CATEGORY") == "categoria"
    assert sem.resolved("ID") == "id_venta"
    assert sem.status("COMPANY") == "missing"     # never invented
