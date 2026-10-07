"""'Herramientas avanzadas': the original main menu. An invalid option never closes the program."""
from app.analysis import full
from app.ui import guided, lab_mode, prompts, render, sql_mode

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
