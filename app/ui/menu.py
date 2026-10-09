"""'Herramientas avanzadas': the original main menu. An invalid option never closes the program."""
import config
from app.analysis import full
from app.errors import AppError
from app.spark.loader import resolve_path
from app.ui import etl as etl_ui
from app.ui import guided, lab_mode, prompts, render, sql_mode
from app.ui import relations as relations_ui

OPTIONS = [
    ("1", "Analizar dataset (resumen y roles semanticos)"),
    ("2", "Hacer pregunta"),
    ("3", "Modo laboratorio (respuesta rapida)"),
    ("4", "Ver esquema"),
    ("5", "Ver perfil"),
    ("6", "Ver historial"),
    ("7", "Exportar evidencia (JSON y CSV)"),
    ("8", "Ejecutar Spark SQL"),
    ("9", "Analisis guiado"),
    ("10", "Analisis completo"),
    ("11", "Registrar dataset adicional (catalogo)"),
    ("12", "Ver datasets registrados"),
    ("13", "Relaciones entre datasets"),
    ("14", "Cambiar ambito de las preguntas (dataset activo / catalogo con JOINs)"),
    ("0", "Volver al menu principal"),
]


def run(session):
    while True:
        render.banner("HERRAMIENTAS AVANZADAS")
        print(f"Dataset: {session.load.path.name} | Registros: {session.profile.rows:,} | "
              f"Columnas: {len(session.profile.columns)} | Spark: LISTO".replace(",", "."))
        print()
        for key, label in OPTIONS:
            print(f"  {key:>2}. {label}")
        choice = prompts.ask("\nSeleccione una opcion: ").strip()
        if choice == "0":
            return
        if choice == "1":
            render.load_summary(session.load, session.profile)
            render.semantic_roles(session.semantic)
        elif choice == "2":
            text = prompts.read_block("\nPregunta (Enter en linea vacia para terminar):\n> ", "  ")
            if text.strip():
                lab_mode.ask_one(session, text, compact=False)
        elif choice == "3":
            lab_mode.run(session)
            continue
        elif choice == "4":
            render.schema(session.profile)
        elif choice == "5":
            render.profile_detail(session.profile)
        elif choice == "6":
            render.history(session.history)
        elif choice == "7":
            lab_mode.export(session)
        elif choice == "8":
            sql_mode.run(session)
            continue
        elif choice == "9":
            guided.run(session)
            continue
        elif choice == "10":
            full_analysis(session)
        elif choice == "11":
            register_dataset(session)
        elif choice == "12":
            show_datasets(session)
        elif choice == "13":
            relations_ui.run(session)
            continue
        elif choice == "14":
            switch_scope(session)
        else:
            print("Opcion no valida. Escriba un numero del menu.")
            continue
        prompts.ask("\n(Enter para volver al menu)")


def full_analysis(session):
    render.banner("ANALISIS COMPLETO")

    def show(i, total, label):
        render.progress(i, total, label)

    results = full.run(session, progress=show, on_result=render.step_result)
    failed = sum(1 for r in results if r.error)
    print(f"\n{len(results)} analisis ejecutados" + (f", {failed} con error" if failed else "") +
          ". Las consultas SQL y la evidencia completa estan en el historial (opcion 6) y en la exportacion (opcion 7).")


def register_dataset(session):
    """Loads another file as a catalog table (its own view); the active dataset does not change."""
    render.banner("REGISTRAR DATASET ADICIONAL")
    print(f"El dataset nuevo queda en su propia vista SQL (su alias). La vista '{config.VIEW_NAME}' no cambia.")
    raw = prompts.ask("\nRuta del dataset (Enter = cancelar): ").strip()
    if not raw:
        return
    try:
        path = resolve_path(raw)
        suggested = session.catalog.suggest_alias(path)
        alias = prompts.ask(f"Alias (Enter = {suggested}): ").strip() or suggested
        session.catalog.check_alias(session.spark, alias)      # before asking anything else
        clean = prompts.confirm_yes("Ejecutar el ETL (limpieza y validacion) sobre este dataset?")
        print("Cargando dataset...")
        entry = session.catalog.register(session.spark, alias, path,
                                         etl=(lambda load, view: etl_ui.run(session.spark, load, view)) if clean else None)
    except AppError as exc:
        render.error(exc)
        return
    print(f"\nDataset '{entry.alias}' registrado: {entry.profile.rows:,} registros, {len(entry.profile.columns)} "
          f"columnas.".replace(",", ".") + f" Consultelo con: SELECT * FROM {entry.view}")


def show_datasets(session):
    print(f"\nDataset activo: {session.load.path.name} -> vista {config.VIEW_NAME}")
    if not len(session.catalog):
        print("No hay datasets adicionales registrados (opcion 11).")
        return
    rows = [[e.alias, ", ".join(e.views), e.load.path.name, e.profile.rows, len(e.profile.columns),
             "si" if e.etl else "no"] for e in session.catalog]
    render.table(["alias", "vistas SQL", "archivo", "registros", "columnas", "ETL"], rows, width=60)


def switch_scope(session):
    """Questions answered on the active dataset (default) or on the catalog tables with JOINs."""
    from app.session import CATALOG_SCOPE, DATASET_SCOPE
    if session.scope == CATALOG_SCOPE:
        session.scope = DATASET_SCOPE
        print(f"\nLas preguntas se responden sobre el dataset activo (vista {config.VIEW_NAME}).")
        return
    if not len(session.catalog):
        print("\nNo hay datasets en el catalogo (opcion 11). El ambito sigue siendo el dataset activo.")
        return
    session.scope = CATALOG_SCOPE
    confirmed = session.relations.confirmed()
    print(f"\nLas preguntas se responden sobre el catalogo: {', '.join(e.alias for e in session.catalog)}.")
    print(f"Relaciones confirmadas que se pueden usar para JOIN: {len(confirmed)}"
          + "".join(f"\n  - {r.label()}" for r in confirmed))
    print("Para volver al dataset activo use de nuevo esta opcion.")
