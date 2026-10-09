"""Exports the session evidence to small JSON and CSV files (standard library only)."""
import csv
import datetime
import json

import config
from app.questions.validator import claim_text


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


TYPE_NAMES = {"OPEN": "Abierta", "MULTIPLE_CHOICE": "Seleccion multiple", "TRUE_FALSE": "Verdadero/Falso"}
RULE = "-" * 60


def workshop_summary(session):
    """Counts of the workshop (shown on screen and written to resumen.txt)."""
    from app.questions import validator as v

    questions = session.questions
    by_status = {status: sum(1 for e in questions if e.validation == status) for status in v.STATUSES}
    etl = session.etl
    return {
        "dataset": session.load.path.name,
        "preguntas": len(questions),
        "correctas": by_status[v.CORRECT],
        "incorrectas": by_status[v.INCORRECT],
        "sin_respuesta_del_estudiante": by_status[v.NO_ANSWER],
        "no_determinadas": by_status[v.UNDETERMINED],
        "no_resueltas": by_status[v.UNRESOLVED],
        "consultas_ejecutadas": sum((1 if e.sql else 0) + len(e.extra_sql) for e in session.history),
        "errores_sql": session.sql_errors,
        "registros_originales": etl.original_rows if etl else session.load.rows,
        "registros_validos": etl.valid_rows if etl else session.load.rows,
        "registros_rechazados": etl.rejected_rows if etl else 0,
        "etl_completado": etl is not None,
        "calidad_validada": bool(etl and etl.rule_results),
    }


def export_workshop(session, folder=None):
    """exports/taller_<timestamp>/ with taller.sql, respuestas.csv, evidencia.json,
    evidencia.txt, resumen.txt and etl_report.json. Returns the folder."""
    base = folder or _writable_folder()
    target = base / f"taller_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    n = 2
    while target.exists():
        target = base / f"taller_{datetime.datetime.now():%Y%m%d_%H%M%S}_{n}"
        n += 1
    target.mkdir(parents=True)
    summary = workshop_summary(session)
    questions = session.questions
    others = [e for e in session.history if e.source != "pregunta"]

    (target / "taller.sql").write_text(_sql_file(session, questions, others), encoding="utf-8-sig")
    with (target / "respuestas.csv").open("w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel
        writer = csv.writer(f)
        writer.writerow(["numero", "pregunta", "tipo", "resultado", "respuesta_correcta", "respuesta_seleccionada",
                         "estado", "explicacion", "advertencias", "sql", "timestamp"])
        for e in questions:
            writer.writerow([e.number, e.question, TYPE_NAMES.get(e.question_type, e.question_type), e.result_text,
                             e.correct_answer, e.selected_answer, e.validation, e.validation_note,
                             " | ".join(e.warnings), e.sql, e.timestamp])
    data = {"generado": datetime.datetime.now().isoformat(timespec="seconds"), "resumen": summary,
            "etl": session.etl.to_dict() if session.etl else None,
            "preguntas": [e.to_dict() for e in questions], "otras_consultas": [e.to_dict() for e in others]}
    (target / "evidencia.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (target / "evidencia.txt").write_text(_evidence_text(session, questions, others), encoding="utf-8-sig")
    (target / "resumen.txt").write_text(summary_text(summary), encoding="utf-8-sig")
    etl = session.etl.to_dict() if session.etl else {"etl": "no ejecutado", "registros": session.load.rows}
    etl["archivo"] = str(session.load.path.name)
    (target / "etl_report.json").write_text(json.dumps(etl, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target


def summary_text(summary):
    ok = lambda flag: "Completado" if flag else "No ejecutado"   # noqa: E731
    lines = ["=" * 60, "RESUMEN DEL TALLER".center(60), "=" * 60, "",
             f"Dataset:                    {summary['dataset']}",
             f"Registros originales:       {summary['registros_originales']}",
             f"Registros validos:          {summary['registros_validos']}",
             f"Registros rechazados:       {summary['registros_rechazados']}", "",
             f"Preguntas:                  {summary['preguntas']}",
             f"Correctas:                  {summary['correctas']}",
             f"Incorrectas:                {summary['incorrectas']}",
             f"Sin respuesta del alumno:   {summary['sin_respuesta_del_estudiante']}",
             f"No determinadas:            {summary['no_determinadas']}",
             f"No resueltas:               {summary['no_resueltas']}", "",
             f"Consultas ejecutadas:       {summary['consultas_ejecutadas']}",
             f"Errores SQL:                {summary['errores_sql']}", "",
             f"ETL:                        {ok(summary['etl_completado'])}",
             f"Calidad (reglas):           {'Validada' if summary['calidad_validada'] else 'Sin reglas del taller'}",
             "=" * 60]
    return "\n".join(lines) + "\n"


def question_text(e):
    """Readable block of one question (evidencia.txt and the screen use the same content)."""
    lines = [RULE, f"PREGUNTA {e.number}", RULE, "", "Pregunta:", e.question, "",
             f"Tipo: {TYPE_NAMES.get(e.question_type, e.question_type)}", ""]
    if e.sql:
        lines += ["SQL:", e.sql + ";", ""]
        lines += [f"SQL adicional (empates): {x};" for x in e.extra_sql]
    lines += [f"Resultado: {e.result_text}"]
    if e.claim is not None and e.question_type == "TRUE_FALSE":
        lines += [f"Valor afirmado: {claim_text(e)}"]
    if e.options:
        lines += ["", "Opciones:"] + [f"  {letter}) {text}" for letter, text in e.options]
    lines += ["", f"Respuesta correcta:     {e.correct_answer or '-'}",
              f"Respuesta seleccionada: {e.selected_answer or '-'}"]
    if e.material_option:
        lines.append(f"Opcion marcada en el material (no usada para validar): {e.material_option}")
    lines += ["", f"Validacion: {e.validation}"]
    lines += [f"Aclaracion: {c}" for c in e.clarifications]
    if e.validation_note:
        lines.append(f"Explicacion: {e.validation_note}")
    lines.append(f"Interpretacion: {e.interpretation}")
    if e.tables_used:
        lines.append(f"Tablas: {', '.join(e.tables_used)}")
        lines += [f"JOIN: {j}" for j in e.joins] + [f"Relacion confirmada: {r}" for r in e.relations_used]
    lines += [f"Advertencia: {w}" for w in e.warnings]
    lines += [f"Fecha y hora: {e.timestamp}", ""]
    return "\n".join(lines)


def _evidence_text(session, questions, others):
    parts = ["EVIDENCIA DEL TALLER - PySpark Lab Analyzer", f"Dataset: {session.load.path.name}",
             f"Generado: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    if session.etl:
        parts += [etl_text(session.etl), ""]
    parts += [question_text(e) for e in questions]
    if others:
        parts += [RULE, "OTRAS CONSULTAS (analisis y SQL manual)", RULE]
        parts += [f"[{e.number}] {e.question}\n{e.sql};\nResultado: {e.result_text}\n" for e in others]
    return "\n".join(parts)


def etl_text(etl):
    lines = ["=" * 60, "ETL".center(60), "=" * 60,
             f"Registros originales:   {etl.original_rows}",
             f"Registros validos:      {etl.valid_rows}",
             f"Registros rechazados:   {etl.rejected_rows}",
             f"Duplicados eliminados:  {etl.duplicates_removed} (encontrados: {etl.duplicates_found})"]
    if etl.transformations:
        lines.append("Transformaciones:")
        for t in etl.transformations:
            lines.append(f"  - {t.description}" + (f" ({t.affected} valores)" if t.affected else ""))
            lines += [f"      {d}" for d in t.details[:10]]
    if etl.rule_results:
        lines.append("Reglas de calidad:")
        lines += [f"  - {r.text}: {r.failed} incumplen, {r.unknown} sin evaluar (nulos)" for r in etl.rule_results]
    for column, count in etl.invalid_values.items():
        lines.append(f"  - {count} valor(es) no validos en {column}")
    lines += [f"Aviso: {w}" for w in etl.warnings]
    return "\n".join(lines)


def _sql_file(session, questions, others):
    parts = [f"-- Consultas Spark SQL del taller ({session.load.path.name})",
             f"-- Vista: {config.VIEW_NAME} (datos limpios). Generado: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    for e in questions:
        first = e.question.splitlines()[0] if e.question else ""
        parts.append(f"-- Pregunta {e.number}: {first}")
        parts.append(f"-- Estado: {e.validation} | Resultado: {e.result_text}")
        parts.append((e.sql + ";") if e.sql else "-- (sin consulta: no se pudo resolver)")
        parts += [x + ";" for x in e.extra_sql]
        parts.append("")
    if others:
        parts.append("-- Otras consultas (analisis guiado/completo y SQL manual)")
        for e in others:
            parts += [f"-- {e.question.splitlines()[0]}", e.sql + ";", ""]
    return "\n".join(parts)


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
