"""PySpark Lab Analyzer.

Normal use (in the lab):  run.cmd [dataset] [--lab] [--debug]
Direct use:               python main.py [dataset] [--lab] [--debug] [--check [--json]]
"""
import argparse
import logging
import sys
import traceback

from app.errors import AppError


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="Analizador de datasets con PySpark y Spark SQL.")
    parser.add_argument("dataset", nargs="?", help="ruta del dataset (CSV, JSON o Parquet)")
    parser.add_argument("--lab", action="store_true", help="iniciar en Modo Laboratorio (respuesta rapida)")
    parser.add_argument("--debug", action="store_true", help="mostrar informacion tecnica adicional")
    parser.add_argument("--check", action="store_true", help="diagnosticar el entorno y salir")
    parser.add_argument("--json", action="store_true", help="con --check: emitir tambien resultados en JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("py4j").setLevel(logging.WARNING)

    try:
        if args.check:
            from app.environment import print_report, run_checks
            results = run_checks()
            print_report(results, as_json=args.json)
            return 1 if any(r["status"] == "ERROR" for r in results) else 0

        from app.application import Application
        return Application(debug=args.debug).run(args.dataset, lab=args.lab)
    except AppError as exc:
        print(f"\nERROR: {exc.message}")
        if exc.hint:
            print(f"  Sugerencia: {exc.hint}")
        return 1
    except Exception as exc:
        print(f"\nError inesperado: {exc}")
        if args.debug:
            traceback.print_exc()
        else:
            print("  Ejecute con --debug para ver el detalle tecnico.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
