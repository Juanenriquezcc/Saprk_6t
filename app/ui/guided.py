"""'Analisis guiado': menus that build a QuerySpec with app.analysis.guided."""
from app.analysis import guided
from app.errors import AppError
from app.ui import prompts, render

OPERATIONS = [
    ("1", "Estadisticas de columna"),
    ("2", "Agrupacion"),
    ("3", "Filtro (ver registros)"),
    ("4", "Ranking"),
    ("5", "Valor maximo/minimo"),
    ("6", "Comparacion entre columnas"),
    ("7", "Variacion porcentual"),
    ("8", "Conteo condicionado"),
    ("0", "Volver"),
]
AGGS = [("Conteo de registros (COUNT)", "COUNT"), ("Suma (SUM)", "SUM"), ("Promedio (AVG)", "AVG"),
        ("Maximo (MAX)", "MAX"), ("Minimo (MIN)", "MIN")]


class _Cancel(Exception):
    pass


def run(session):
    while True:
        render.banner("ANALISIS GUIADO")
        for key, label in OPERATIONS:
            print(f"  {key}. {label}")
        choice = prompts.ask("\nSeleccione una operacion: ").strip()
        if choice == "0":
            return
        builder = BUILDERS.get(choice)
        if builder is None:
            print("Opcion no valida.")
            continue
        try:
            spec, label = builder(session)
            render.evidence(session.run_spec(spec, f"Analisis guiado: {label}"))
        except _Cancel:
            print("Operacion cancelada.")
        except AppError as exc:
            render.error(exc)


# --- pickers ------------------------------------------------------------------

def _need(value):
    if value is None:
        raise _Cancel()
    return value


def _metric(session, message="Seleccione la columna numerica:"):
    cols = [c.name for c in session.profile.columns if c.kind == "numeric" and not c.is_id]
    if not cols:
        raise AppError("El dataset no tiene columnas numericas.")
    return _need(prompts.pick(message, [(c, c) for c in cols]))


def _group_column(session):
    profile = session.profile
    cols = [c.name for c in profile.categorical] + [
        c.name for c in profile.columns
        if not c.is_categorical and (c.kind in ("text", "boolean") or c.is_date)]
    if not cols:
        raise AppError("El dataset no tiene columnas para agrupar (texto o fecha).")
    labels = [(f"{c}  ({'categorica' if profile.column(c).is_categorical else 'muchos valores'})", c) for c in cols]
    return _need(prompts.pick("Seleccione la columna de agrupacion:", labels))


def _aggregation(with_count=True):
    options = AGGS if with_count else AGGS[1:]
    return _need(prompts.pick("Seleccione el calculo:", options))


def _conditions(session):
    filters, date_filters = [], []
    while True:
        column = _need(prompts.pick("Columna de la condicion:", [(c, c) for c in session.column_names]))
        op = _need(prompts.pick("Operador:", [(o, o) for o in guided.OPERATORS]))
        raw = prompts.ask("Valor (o nombre de otra columna): ")
        try:
            cond, date_cond = guided.condition(session.profile, column, op, raw)
        except AppError as exc:
            render.error(exc)
            continue
        (filters if cond else date_filters).append(cond or date_cond)
        if not prompts.confirm("Agregar otra condicion (AND)?"):
            return filters, date_filters


def _labels(filters, date_filters):
    return " y ".join(f.label() for f in (*filters, *date_filters))


# --- operations -----------------------------------------------------------------

def _stats(session):
    column = _need(prompts.pick("Seleccione la columna:", [(c, c) for c in session.column_names]))
    return guided.column_stats(session.profile, column), f"estadisticas de {column}"


def _group(session):
    group_by = _group_column(session)
    agg = _aggregation()
    metric = None if agg == "COUNT" else _metric(session)
    return guided.group(group_by, agg, metric), f"{agg} de {metric or 'registros'} por {group_by}"


def _filter(session):
    filters, date_filters = _conditions(session)
    limit = prompts.ask_int("Cuantos registros mostrar", 20, 1, 100)
    return guided.filter_rows(filters, date_filters, limit), f"registros donde {_labels(filters, date_filters)}"


def _ranking(session):
    group_by = _group_column(session)
    agg = _aggregation()
    metric = None if agg == "COUNT" else _metric(session)
    order = _need(prompts.pick("Orden:", [("Mayor primero", "DESC"), ("Menor primero", "ASC")]))
    n = prompts.ask_int("Cuantos puestos mostrar", 5, 1, 100)
    return guided.ranking(group_by, agg, metric, order, n), f"top {n} de {group_by} por {agg} de {metric or 'registros'}"


def _extreme(session):
    metric = _metric(session)
    which = _need(prompts.pick("Buscar:", [("Maximo", "MAX"), ("Minimo", "MIN")]))
    mode = _need(prompts.pick("Mostrar:", [("Solo el valor", "value"), ("El registro completo", "record")]))
    return guided.extreme(metric, which, mode), f"{which} de {metric} ({'registro' if mode == 'record' else 'valor'})"


def _compare(session):
    a = _metric(session, "Primera columna:")
    b = _metric(session, "Segunda columna:")
    agg = _aggregation(with_count=False)
    return guided.compare(a, b, agg), f"{agg} de {a} frente a {b}"


def _pct_change(session):
    base = _metric(session, "Columna del valor INICIAL:")
    final = _metric(session, "Columna del valor FINAL:")
    agg = _need(prompts.pick("Calculo sobre la variacion de cada registro:",
                             [("Promedio (AVG)", "AVG"), ("Maximo (MAX)", "MAX"), ("Minimo (MIN)", "MIN")]))
    group_by, order = None, None
    if prompts.confirm("Calcular por grupo (por ejemplo, por empresa)?"):
        group_by = _group_column(session)
        if prompts.confirm("Mostrar solo el grupo con mayor valor?"):
            order = "DESC"
    spec = guided.pct_change(base, final, agg, group_by, order, 1 if order else None)
    return spec, f"{agg} de la variacion % de {base} a {final}" + (f" por {group_by}" if group_by else "")


def _count_where(session):
    filters, date_filters = _conditions(session)
    return guided.count_where(filters, date_filters), f"registros donde {_labels(filters, date_filters)}"


BUILDERS = {"1": _stats, "2": _group, "3": _filter, "4": _ranking, "5": _extreme, "6": _compare,
            "7": _pct_change, "8": _count_where}
