"""Exports the session evidence to small JSON and CSV files (standard library only)."""
import csv
import datetime
import json

import config


def export_history(history, folder=None):
    """Writes evidencia_<timestamp>.json/.csv and returns both paths."""
    folder = folder or _writable_folder()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = folder / f"evidencia_{stamp}.json"
    csv_path = folder / f"evidencia_{stamp}.csv"

    json_path.write_text(json.dumps([e.to_dict() for e in history], ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:   # BOM: opens cleanly in Excel
        writer = csv.writer(f)
        writer.writerow(["numero", "pregunta", "tipo", "intencion", "respuesta", "resultado", "valor", "sql",
                         "explicacion", "advertencias", "timestamp"])
        for e in history:
            writer.writerow([e.number, e.question, e.question_type, e.intent, e.answer, e.result_text, e.value,
                             e.sql, e.interpretation, " | ".join(e.warnings), e.timestamp])
    return json_path, csv_path


def _writable_folder():
    for folder in (config.EXPORT_DIR, config.EXPORT_FALLBACK_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write_test"
            probe.write_text("x")
            probe.unlink()
            return folder
        except OSError:
            continue
    raise OSError("No hay una carpeta con permiso de escritura para exportar.")
