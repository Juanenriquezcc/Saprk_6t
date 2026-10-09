"""Relations between catalog tables (phase 2 of JOINs): proposed, measured with Spark, confirmed by the user.

Synthetic fixtures in tests/data/rel, expected values counted by hand:
  clientes  cliente_id 1..5 (unique)                       nombre, ciudad
  pedidos   cliente_id 1,1,2,3,3,3,NULL,9  (8 rows)        producto P1,P2,P1,P3,P1,P2,P2,P3   cantidad
  perfiles  cliente_id 1,2,3,4,6 (unique)                  segmento
  precios   producto P1,P1,P2,P4                           precio
  codigos   cliente_id 'C001','C002','C003' (text)         descuento
  productos producto 'Laptop','Mouse','Silla' (no overlap with pedidos)

  clientes.cliente_id = pedidos.cliente_id   keys in common {1,2,3}
      clientes: 5 rows, 0 nulls, 5 distinct, 0 repeated, orphans 4,5 -> 2, coverage 3/5
      pedidos:  8 rows, 1 null, 4 distinct {1,2,3,9}, repeated 1 and 3 -> 2, orphan 9 -> 1, coverage 6/7
      cardinality 1:N (read from pedidos: N:1)
  clientes.cliente_id = perfiles.cliente_id  common {1,2,3,4}; orphans 5 and 6 -> 1 / 1; 1:1
  pedidos.producto = precios.producto        common {P1,P2}; pedidos P3 x2 orphans, precios P4 orphan;
      P1 is 3 rows in pedidos and 2 in precios -> N:M; repeated keys 3 (P1,P2,P3) / 1 (P1)
"""
import socket

import pytest

import config
from app.catalog import DatasetCatalog
from app.errors import AppError
from app.schema import relations as rl
from app.schema.profiler import ColumnProfile
from app.schema.semantic import SemanticMap
from app.spark.loader import CsvOptions
from conftest import DATA

REL = DATA / "rel"


@pytest.fixture
def catalog(spark):
    cat = DatasetCatalog()

    def add(*names):
        for name in names:
            # productos.csv has only text columns: the sniffer cannot tell its header, so it is given.
            cat.register(spark, name, REL / f"{name}.csv", csv_options=CsvOptions(header=True))
        return cat

    cat.add = add
    yield cat
    for entry in cat:
        cat.remove(spark, entry.alias)


def measured(cat, left, left_column, right, right_column):
    relation = cat.relations.add(cat, left, left_column, right, right_column)
    cat.relations.measure(cat, relation)
    return relation


def sides(metrics):
    return [(s.table, s.rows, s.nulls, s.distinct, s.duplicate_keys, s.orphans) for s in (metrics.left, metrics.right)]


def test_one_to_many_with_null_keys_and_orphans_on_both_sides(catalog):
    m = measured(catalog.add("clientes", "pedidos"), "clientes", "cliente_id", "pedidos", "cliente_id").metrics
    assert m.compatible and m.matched_keys == 3 and m.cardinality == "1:N" and not m.many_to_many
    assert sides(m) == [("clientes", 5, 0, 5, 0, 2), ("pedidos", 8, 1, 4, 2, 1)]    # the null key is not an orphan
    assert m.left.coverage == pytest.approx(3 / 5) and m.right.coverage == pytest.approx(6 / 7)
    assert (m.left.max_per_key, m.right.max_per_key) == (1, 3)
    assert m.blockers() == []
    warnings = " ".join(m.warnings())
    for text in ("2 registro(s) de clientes sin correspondencia en pedidos",
                 "1 registro(s) de pedidos sin correspondencia en clientes",
                 "pedidos.cliente_id: 1 registro(s) con clave nula", "pedidos.cliente_id tiene 2 valor(es) de clave repetidos"):
        assert text in warnings


def test_many_to_one_is_the_same_relation_read_from_the_other_side(catalog):
    m = measured(catalog.add("clientes", "pedidos"), "pedidos", "cliente_id", "clientes", "cliente_id").metrics
    assert m.cardinality == "N:1" and m.matched_keys == 3
    assert (m.left.orphans, m.right.orphans) == (1, 2)


def test_one_to_one(catalog):
    m = measured(catalog.add("clientes", "perfiles"), "clientes", "cliente_id", "perfiles", "cliente_id").metrics
    assert m.cardinality == "1:1" and m.matched_keys == 4
    assert sides(m) == [("clientes", 5, 0, 5, 0, 1), ("perfiles", 5, 0, 5, 0, 1)]
    assert m.left.coverage == m.right.coverage == pytest.approx(0.8)


def test_many_to_many_is_flagged_and_never_altered(spark, catalog):
    catalog.add("pedidos", "precios")
    relation = measured(catalog, "pedidos", "producto", "precios", "producto")
    m = relation.metrics
    assert m.cardinality == "N:M" and m.many_to_many and m.matched_keys == 2
    assert sides(m) == [("pedidos", 8, 0, 3, 3, 2), ("precios", 4, 0, 3, 1, 1)]
    assert (m.left.max_per_key, m.right.max_per_key) == (3, 2)
    assert any("NO es segura para sumar" in w for w in m.warnings())
    with pytest.raises(AppError, match="advertencias"):
        catalog.relations.confirm(relation)                      # never silently
    catalog.relations.confirm(relation, accept_warnings=True)
    assert relation.status == rl.CONFIRMED and relation.metrics.many_to_many    # still flagged as unsafe
    assert [spark.table(t).count() for t in ("pedidos", "precios")] == [8, 4]    # no row removed


def test_incompatible_types_cannot_be_confirmed(catalog):
    catalog.add("clientes", "codigos")
    relation = measured(catalog, "clientes", "cliente_id", "codigos", "cliente_id")
    m = relation.metrics
    assert not m.compatible and (m.left.dtype, m.right.dtype) == ("int", "string")
    assert m.matched_keys is None and m.cardinality is None and m.left.orphans is None
    assert "Tipos incompatibles" in m.blockers()[0]
    with pytest.raises(AppError, match="Tipos incompatibles"):
        catalog.relations.confirm(relation, accept_warnings=True)
    assert relation.status == rl.PENDING
    assert rl.type_family("bigint") == rl.type_family("decimal(10,2)") == "numero" and rl.type_family("string") == "texto"


def test_same_name_but_no_common_values(catalog):
    catalog.add("pedidos", "productos")
    proposed = catalog.relations.propose(catalog)
    assert [r.label() for r in proposed] == ["pedidos.producto = productos.producto"]   # proposed by its name
    relation = proposed[0]
    m = catalog.relations.measure(catalog, relation)
    assert m.compatible and m.matched_keys == 0 and m.cardinality is None
    assert (m.left.orphans, m.right.orphans) == (8, 3) and m.left.coverage == 0
    with pytest.raises(AppError, match="Ninguna clave coincide"):
        catalog.relations.confirm(relation, accept_warnings=True)


def test_confirm_reject_and_pending(catalog):
    catalog.add("clientes", "pedidos")
    relations = catalog.relations
    relation = relations.propose(catalog)[0]
    assert relation.label() == "clientes.cliente_id = pedidos.cliente_id" and relation.status == rl.PENDING
    with pytest.raises(AppError, match="Mida la relacion"):
        relations.confirm(relation, accept_warnings=True)
    relations.measure(catalog, relation)
    with pytest.raises(AppError, match="advertencias"):
        relations.confirm(relation)
    assert relations.confirmed() == [] and relations.pending() == [relation]
    relations.confirm(relation, accept_warnings=True)
    assert relations.confirmed() == [relation]
    relations.reject(relation)
    assert relations.confirmed() == [] and relations.with_status(rl.REJECTED) == [relation]
    relations.propose(catalog)                                        # proposing again keeps decisions
    assert relation.status == rl.REJECTED and len(relations) == 1
    relations.leave_pending(relation)
    assert relations.pending() == [relation]
    assert relations.find("pedidos", "cliente_id", "clientes", "cliente_id") is relation    # either direction


def test_removing_a_table_removes_its_relations(spark, catalog):
    catalog.add("clientes", "pedidos", "perfiles")
    relations = catalog.relations
    relations.propose(catalog)
    keep = measured(catalog, "clientes", "cliente_id", "perfiles", "cliente_id")
    relations.confirm(keep, accept_warnings=True)
    relations.confirm(measured(catalog, "clientes", "cliente_id", "pedidos", "cliente_id"), accept_warnings=True)
    catalog.remove(spark, "pedidos")
    assert not any(r.involves("pedidos") for r in relations) and relations.confirmed() == [keep]
    catalog.add("pedidos")                                            # same alias, new data: nothing inherited
    again = relations.add(catalog, "clientes", "cliente_id", "pedidos", "cliente_id")
    assert again.status == rl.PENDING and again.metrics is None


def test_four_datasets_without_mixing_results(spark, catalog):
    catalog.add("clientes", "pedidos", "perfiles", "precios")
    views = {t: (spark.table(t).count(), catalog.get(t).df.is_cached) for t in ("clientes", "pedidos", "perfiles", "precios")}
    proposed = catalog.relations.propose(catalog)
    assert [r.label() for r in proposed] == ["clientes.cliente_id = pedidos.cliente_id",
                                             "clientes.cliente_id = perfiles.cliente_id",
                                             "pedidos.cliente_id = perfiles.cliente_id",
                                             "pedidos.producto = precios.producto"]   # never a measure (cantidad, precio)
    for r in proposed:
        catalog.relations.measure(catalog, r)
    got = {r.label(): (r.metrics.left.rows, r.metrics.right.rows, r.metrics.matched_keys, r.metrics.cardinality)
           for r in proposed}
    assert got == {"clientes.cliente_id = pedidos.cliente_id": (5, 8, 3, "1:N"),
                   "clientes.cliente_id = perfiles.cliente_id": (5, 5, 4, "1:1"),
                   "pedidos.cliente_id = perfiles.cliente_id": (8, 5, 3, "N:1"),     # common {1,2,3}
                   "pedidos.producto = precios.producto": (8, 4, 2, "N:M")}
    assert {t: (spark.table(t).count(), catalog.get(t).df.is_cached) for t in views} == views   # views untouched


def test_candidates_are_bounded(catalog, monkeypatch):
    catalog.add("clientes", "pedidos")
    monkeypatch.setattr(config, "RELATION_MAX_CANDIDATES_PER_PAIR", 0)
    assert catalog.relations.propose(catalog) == []


class _Entry:
    """Metadata-only table for the candidate rules (no Spark needed)."""

    def __init__(self, alias, *columns):
        self.alias = alias
        self.profile = type("P", (), {"columns": [ColumnProfile(n, d, k, is_id=i) for n, d, k, i in columns]})()
        self.semantic = SemanticMap()


def test_candidate_rules_from_names():
    customers = _Entry("customers", ("id", "int", "numeric", True), ("name", "string", "text", True))
    orders = _Entry("orders", ("order_id", "int", "numeric", True), ("customer_id", "int", "numeric", True),
                    ("amount", "double", "numeric", False), ("fecha", "date", "date", False))
    sedes = _Entry("ciudades", ("id_ciudad", "int", "numeric", True), ("amount", "double", "numeric", False))
    found = dict(rl.candidates([customers, orders, sedes]))
    assert list(found) == [("customers", "id", "orders", "customer_id")]      # amount = amount is a measure
    assert "'customer_id' parece referirse a la tabla 'customers'" in found[("customers", "id", "orders", "customer_id")][0]
    assert rl._references(["id", "ciudad"], "ciudades") and rl._references(["cliente", "id"], "clientes")
    assert not rl._references(["cliente"], "clientes") and not rl._references(["producto", "id"], "clientes")


def test_relations_work_offline(catalog, monkeypatch):
    real_connect, outside = socket.socket.connect, []

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            outside.append(address)
            raise OSError("red deshabilitada en la prueba")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    catalog.add("clientes", "perfiles")
    relation = catalog.relations.propose(catalog)[0]
    assert catalog.relations.measure(catalog, relation).cardinality == "1:1" and outside == []


def test_explanation_for_the_user(catalog):
    relation = measured(catalog.add("clientes", "pedidos"), "clientes", "cliente_id", "pedidos", "cliente_id")
    text = "\n".join(rl.explain(relation))
    assert "Cardinalidad observada: 1:N (uno en clientes, varios en pedidos)" in text
    assert "no una garantia para datos futuros" in text and "60.0%" in text and "85.7%" in text


MENU_SCRIPT = [
    "13",                                  # Relaciones entre datasets
    "1", "1", "1", "s",                    #   candidates -> #1 -> Confirmar -> accept the warnings
    "",                                    #   back from the candidate list
    "2", "1", "3", "1", "3", "1",          #   manual pair clientes.ciudad = pedidos.producto -> Rechazar
    "",
    "3", "",                               #   list every relation
    "0",                                   #   back to the advanced menu
    "0",
]


def test_menu_reviews_relations(lab, spark, monkeypatch, capsys):
    from app.ui import menu

    session = lab("stocks_small.csv")
    try:
        for name in ("clientes", "pedidos"):
            session.catalog.register(spark, name, REL / f"{name}.csv")
        answers = iter(MENU_SCRIPT)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        menu.run(session)
        out = capsys.readouterr().out
        assert next(answers, None) is None and "Traceback" not in out
        assert "Cardinalidad observada: 1:N" in out and "BLOQUEO: Ninguna clave coincide" in out
        assert "Relacion clientes.cliente_id = pedidos.cliente_id: CONFIRMADA" in out
        assert "Relacion clientes.ciudad = pedidos.producto: RECHAZADA" in out
        assert [r.label() for r in session.relations.confirmed()] == ["clientes.cliente_id = pedidos.cliente_id"]
    finally:
        for entry in session.catalog:
            session.catalog.remove(spark, entry.alias)
