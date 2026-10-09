"""Dataset catalog: several tables loaded at once, each in its own Spark view (phase 1 of JOINs).

Synthetic fixtures already in tests/data (expected values written by hand in their tests):
  stocks_small.csv    12 rows   Date, Company, Open, ..., Volume
  ventas.csv           6 rows   fecha, categoria, cantidad, precio_unitario, precio_total
  ventas_unidades.csv 10 rows   SUM(quantity) = 45
  ventas_sucio.csv    16 rows   after the ETL of test_etl.py: 8 valid, 7 rejected, 1 duplicate removed
"""
import socket

import pytest

import config
from app.catalog import DatasetCatalog
from app.errors import AppError
from app.etl import pipeline
from app.etl.rules import parse_rules
from conftest import DATA

RULES = "quantity entre 1 y 20\ncustomer_age BETWEEN 18 AND 100\nreturned_qty <= quantity\naño(order_date) = 2025"
ETL_ANSWERS = {"transform": "all", "unknown": True, "duplicates": True}
SINGLE_FLOW_VIEWS = ("dataset", "dataset_original", "rechazados")


def clean(spark):
    return lambda load, view: pipeline.run(spark, load, parse_rules(RULES), lambda d: ETL_ANSWERS[d.key], view=view)


@pytest.fixture
def catalog(spark):
    cat = DatasetCatalog()
    yield cat
    for entry in cat:                         # the Spark session is shared by the whole test run
        cat.remove(spark, entry.alias)


@pytest.fixture
def four(spark, catalog):
    catalog.register(spark, "acciones", DATA / "stocks_small.csv")
    catalog.register(spark, "ventas", DATA / "ventas.csv")
    catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv")
    catalog.register(spark, "sucio", DATA / "ventas_sucio.csv", etl=clean(spark))
    return catalog


def count(spark, view):
    return spark.sql(f"SELECT COUNT(*) FROM {view}").first()[0]


def snapshot(spark):
    """Row count of every view of the single-dataset flow (None = not registered)."""
    return {v: count(spark, v) if spark.catalog.tableExists(v) else None for v in SINGLE_FLOW_VIEWS}


def test_four_datasets_at_once_each_with_its_own_data(spark, four):
    assert [e.alias for e in four] == ["acciones", "ventas", "pedidos", "sucio"] and len(four) == 4
    expected = {"acciones": (12, "Volume"), "ventas": (6, "precio_total"), "pedidos": (10, "quantity"), "sucio": (8, "city")}
    for alias, (rows, column) in expected.items():
        entry = four.get(alias)
        assert entry.view == alias and count(spark, alias) == rows == entry.profile.rows
        assert column in spark.table(alias).columns and column in [c.name for c in entry.profile.columns]
    assert spark.sql("SELECT SUM(quantity) FROM pedidos").first()[0] == 45
    assert four.get("PEDIDOS ").alias == "pedidos"                       # aliases are case-insensitive


def test_views_are_distinct_and_hold_the_right_data(spark, four):
    views = [v for e in four for v in e.views]
    assert len(views) == len(set(views)) == 6
    assert four.get("sucio").views == ("sucio", "sucio_original", "sucio_rechazados")
    assert (count(spark, "sucio_original"), count(spark, "sucio_rechazados")) == (16, 7)
    assert set(spark.table("acciones").columns).isdisjoint({"categoria", "quantity"})
    # Manual SQL can combine tables by alias (JOINs from questions come in a later phase).
    joined = spark.sql("SELECT COUNT(*) FROM pedidos p JOIN pedidos q ON p.order_id = q.order_id").first()[0]
    assert joined == 10


def test_load_profile_and_etl_stay_with_their_dataset(four):
    sucio, pedidos, acciones = four.get("sucio"), four.get("pedidos"), four.get("acciones")
    assert sucio.load.path.name == "ventas_sucio.csv" and sucio.load.rows == 16
    assert (sucio.etl.valid_rows, sucio.etl.rejected_rows, sucio.etl.duplicates_removed) == (8, 7, 1)
    assert sucio.df is sucio.etl.df and sucio.etl.to_dict()["vistas_sql"]["rechazados"] == "sucio_rechazados"
    assert sucio.etl.null_counts                                         # filled from its own profile
    assert pedidos.etl is None and pedidos.df is pedidos.load.df and pedidos.views == ("pedidos",)
    assert pedidos.semantic.resolved("QUANTITY") == "quantity" and acciones.semantic.resolved("VOLUME") == "Volume"
    assert acciones.semantic.resolved("QUANTITY") is None


def test_registering_another_dataset_leaves_the_first_untouched(spark, catalog):
    first = catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv")
    before = (first.load, first.profile, first.semantic, list(spark.table("pedidos").columns), count(spark, "pedidos"))
    catalog.register(spark, "acciones", DATA / "stocks_small.csv")
    catalog.register(spark, "sucio", DATA / "ventas_sucio.csv", etl=clean(spark))
    after = catalog.get("pedidos")
    assert after is first
    assert (after.load, after.profile, after.semantic, list(spark.table("pedidos").columns), count(spark, "pedidos")) == before


def test_duplicate_alias_is_rejected_without_overwriting(spark, catalog):
    original = catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv")
    for alias in ("pedidos", " Pedidos "):
        with pytest.raises(AppError, match="Ya existe un dataset con el alias 'pedidos'"):
            catalog.register(spark, alias, DATA / "stocks_small.csv")
    assert catalog.get("pedidos") is original and len(catalog) == 1
    assert count(spark, "pedidos") == 10 and "quantity" in spark.table("pedidos").columns


@pytest.mark.parametrize("alias", ["", "   ", None, "1ventas", "_ventas", "con espacio", "tabla-x", "pedidos.csv",
                                   "ñandu", "select", "join", "order", "a" * 41])
def test_invalid_alias_is_rejected_before_loading(spark, catalog, alias):
    with pytest.raises(AppError, match="Alias no valido"):
        catalog.register(spark, alias, DATA / "ventas_unidades.csv")
    assert len(catalog) == 0


@pytest.mark.parametrize("alias", ["dataset", "dataset_original", "rechazados", "DATASET"])
def test_views_of_the_single_dataset_flow_are_reserved(spark, catalog, alias):
    before = snapshot(spark)
    with pytest.raises(AppError, match="usaria la vista"):
        catalog.register(spark, alias, DATA / "ventas_unidades.csv")
    assert snapshot(spark) == before and len(catalog) == 0


def test_no_collision_with_auxiliary_or_existing_views(spark, catalog):
    catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv")
    with pytest.raises(AppError, match="usaria la vista 'pedidos_original'"):     # 'pedidos' owns pedidos_original
        catalog.register(spark, "pedidos_original", DATA / "stocks_small.csv")
    catalog.register(spark, "ventas", DATA / "ventas.csv")
    with pytest.raises(AppError, match="usaria la vista 'ventas_rechazados'"):
        catalog.register(spark, "ventas_rechazados", DATA / "stocks_small.csv")
    spark.range(3).createOrReplaceTempView("vista_externa")                       # created outside the catalog
    try:
        with pytest.raises(AppError, match="usaria la vista 'vista_externa'"):
            catalog.register(spark, "vista_externa", DATA / "stocks_small.csv")
        assert count(spark, "vista_externa") == 3
    finally:
        spark.catalog.dropTempView("vista_externa")
    assert [e.alias for e in catalog] == ["pedidos", "ventas"]


def test_failed_registration_leaves_nothing(spark, catalog):
    def broken_etl(load, view):
        raise AppError("ETL cancelado")
    with pytest.raises(AppError, match="ETL cancelado"):
        catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv", etl=broken_etl)
    assert len(catalog) == 0 and not spark.catalog.tableExists("pedidos")
    assert catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv").profile.rows == 10   # alias free again


def test_remove_drops_the_views(spark, catalog):
    catalog.register(spark, "sucio", DATA / "ventas_sucio.csv", etl=clean(spark))
    catalog.remove(spark, "sucio")
    assert len(catalog) == 0 and not any(spark.catalog.tableExists(v)
                                         for v in ("sucio", "sucio_original", "sucio_rechazados"))
    with pytest.raises(AppError, match="No hay ningun dataset"):
        catalog.get("sucio")


def test_suggested_alias(spark, catalog, tmp_path):
    assert catalog.suggest_alias(DATA / "ventas_unidades.csv") == "ventas_unidades"
    assert catalog.suggest_alias(tmp_path / "Ventas Colombia (2025).csv") == "ventas_colombia_2025"
    assert catalog.suggest_alias(tmp_path / "2025.csv") == "t_2025"
    assert catalog.suggest_alias(tmp_path / "dataset.csv") == "dataset_2"          # never a reserved view
    assert catalog.suggest_alias(tmp_path / "select.csv") == "t_select"
    catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv")
    assert catalog.suggest_alias(tmp_path / "pedidos.json") == "pedidos_2"
    for name in ("Ventas Colombia (2025).csv", "2025.csv", "dataset.csv", "select.csv"):
        assert catalog.check_alias(spark, catalog.suggest_alias(tmp_path / name))   # always usable


def test_single_dataset_flow_is_unchanged(spark, lab, catalog):
    session = lab("stocks_small.csv")
    assert len(session.catalog) == 0 and pipeline.etl_views() == SINGLE_FLOW_VIEWS
    before = snapshot(spark)
    catalog.register(spark, "pedidos", DATA / "ventas_unidades.csv")
    catalog.register(spark, "sucio", DATA / "ventas_sucio.csv", etl=clean(spark))
    assert snapshot(spark) == before                                    # dataset / dataset_original / rechazados
    ev = session.ask("¿Cuántos registros hay?", lambda need: None)
    assert ev.value == 12 and f"FROM {config.VIEW_NAME}" in ev.sql


def test_catalog_works_offline(spark, catalog, monkeypatch):
    """Registering only talks to the local Spark JVM (py4j on loopback): never to the network."""
    real_connect, outside = socket.socket.connect, []

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            outside.append(address)
            raise OSError("red deshabilitada en la prueba")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    entry = catalog.register(spark, "sucio", DATA / "ventas_sucio.csv", etl=clean(spark))
    assert entry.profile.rows == 8 and outside == []


MENU_SCRIPT = [
    "12", "",                                             # empty catalog
    "11", str(DATA / "ventas_unidades.csv"), "", "n", "",  # suggested alias, no ETL
    "11", str(DATA / "ventas_sucio.csv"), "dataset",      # reserved alias -> error, back to the menu
    "",
    "12", "",
    "0",
]


def test_menu_registers_and_lists(lab, spark, monkeypatch, capsys):
    from app.ui import menu

    session = lab("stocks_small.csv")
    answers = iter(MENU_SCRIPT)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    try:
        menu.run(session)
        out = capsys.readouterr().out
        assert next(answers, None) is None and "Traceback" not in out
        assert "No hay datasets adicionales registrados" in out
        assert "Dataset 'ventas_unidades' registrado: 10 registros" in out
        assert "usaria la vista 'dataset'" in out
        assert [e.alias for e in session.catalog] == ["ventas_unidades"] and count(spark, "dataset") == 12
    finally:
        for entry in session.catalog:
            session.catalog.remove(spark, entry.alias)
