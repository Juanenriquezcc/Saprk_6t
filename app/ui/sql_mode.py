"""'Ejecutar Spark SQL': read-only SQL over the `dataset` view, bounded results."""
import config
from app.errors import AppError
from app.ui import prompts, render

HELP = (f"Escriba una consulta de lectura sobre la vista '{config.VIEW_NAME}' y termine con ';' o una linea vacia.\n"
        f"Se muestran como maximo {config.MANUAL_SQL_MAX_ROWS} filas. Escriba :q para volver al menu.")


def run(session):
    render.banner("EJECUTAR SPARK SQL")
    print(HELP)
    while True:
        sql = prompts.read_sql("\nsql> ", "...> ")
        if not sql.strip():
            continue
        if sql.strip().lower() in prompts.COMMANDS_BACK:
            return
        try:
            render.evidence(session.run_sql(sql))
        except AppError as exc:
            render.error(exc)
