import csv
import json

from app.export import export_history
from conftest import chooser


def test_export_json_and_csv(lab, tmp_path):
    session = lab("stocks_small.csv")
    session.ask("¿Cuántos registros hay?", chooser({}))
    session.ask("Los registros donde Close > Open son 15.", chooser({}))
    json_path, csv_path = export_history(session.history, tmp_path)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert [d["answer"] for d in data] == ["12", "FALSO"]
    assert data[1]["correct_value"] == "8" and "WHERE Close > Open" in data[1]["sql"]
    assert data[0]["spec"]["intent"] == "COUNT_ROWS"

    with csv_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["numero"] for r in rows] == ["1", "2"]
    assert rows[1]["respuesta"] == "FALSO"
