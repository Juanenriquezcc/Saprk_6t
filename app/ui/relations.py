"""'Relaciones entre datasets': review candidates, measure them and confirm, reject or leave pending.

Only confirmed relations will be used by JOINs. Nothing is confirmed without the user.
"""
from app.errors import AppError
from app.schema import relations as rl
from app.ui import prompts, render

OPTIONS = [
    ("1", "Buscar relaciones candidatas"),
    ("2", "Analizar un par de columnas elegido por usted"),
    ("3", "Ver todas las relaciones (confirmadas, pendientes y rechazadas)"),
    ("0", "Volver"),
]


def run(session):
    catalog = session.catalog
    while True:
        render.banner("RELACIONES ENTRE DATASETS")
        if len(catalog) < 2:
            print("Registre al menos dos datasets adicionales (opcion 11) para buscar relaciones.")
            return
        print(f"Datasets: {', '.join(e.alias for e in catalog)} | Confirmadas: {len(catalog.relations.confirmed())}\n")
        for key, label in OPTIONS:
            print(f"  {key}. {label}")
        choice = prompts.ask("\nSeleccione una opcion: ").strip()
        if choice == "0":
            return
        if choice == "1":
            found = catalog.relations.propose(catalog)
            if not found:
                print("\nNo se encontraron candidatas por nombre ni por rol. Use la opcion 2 para indicar las columnas.")
            else:
                review_list(session, found)
                continue
        elif choice == "2":
            relation = choose_pair(session)
            if relation is not None:
                review(session, relation)
        elif choice == "3":
            show(catalog.relations)
        else:
            print("Opcion no valida.")
            continue
        prompts.ask("\n(Enter para continuar)")


def show(relations):
    if not len(relations):
        print("\nTodavia no hay relaciones.")
        return
    rows = [[i, r.label(), r.status, r.metrics.cardinality if r.metrics else "sin medir"] for i, r in enumerate(relations, 1)]
    render.table(["#", "relacion", "estado", "cardinalidad"], rows, width=70)


def review_list(session, found):
    while True:
        print("\nRelaciones candidatas:")
        for i, r in enumerate(found, 1):
            print(f"  {i}. {r.label()}  [{r.status}]  - {'; '.join(r.reasons)}")
        answer = prompts.ask(f"\nNumero de la relacion a revisar (1-{len(found)}; Enter = volver): ").strip()
        if not answer:
            return
        if answer.isdigit() and 1 <= int(answer) <= len(found):
            review(session, found[int(answer) - 1])
        else:
            print("Opcion no valida.")


def choose_pair(session):
    catalog = session.catalog
    tables = [(e.alias, e) for e in catalog]
    left = prompts.pick("Tabla izquierda:", tables)
    if left is None:
        return None
    left_column = prompts.pick(f"Columna de {left.alias}:", [(c, c) for c in left.df.columns])
    if left_column is None:
        return None
    right = prompts.pick("Tabla derecha:", [t for t in tables if t[1] is not left])
    if right is None:
        return None
    right_column = prompts.pick(f"Columna de {right.alias}:", [(c, c) for c in right.df.columns])
    if right_column is None:
        return None
    return catalog.relations.add(catalog, left.alias, left_column, right.alias, right_column, "elegida por el usuario")


def review(session, relation):
    relations = session.catalog.relations
    if relation.metrics is None:
        print("\nMidiendo la relacion con Spark...")
        try:
            relations.measure(session.catalog, relation)
        except AppError as exc:
            render.error(exc)
            return
    print()
    for line in rl.explain(relation):
        print(line)
    blocked = relation.metrics.blockers()
    options = [("Confirmar", rl.CONFIRMED)] if not blocked else []
    options += [("Rechazar", rl.REJECTED), ("Dejar pendiente", rl.PENDING)]
    choice = prompts.pick("Que desea hacer con esta relacion?", options)
    if choice is None:
        return
    if choice == rl.CONFIRMED:
        if relation.metrics.warnings() and not prompts.confirm(
                "La relacion tiene advertencias (ver arriba). Confirmarla de todos modos?"):
            print("No se confirmo: la relacion sigue como estaba.")
            return
        relations.confirm(relation, accept_warnings=True)
    elif choice == rl.REJECTED:
        relations.reject(relation)
    else:
        relations.leave_pending(relation)
    print(f"Relacion {relation.label()}: {relation.status}")
