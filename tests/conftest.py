import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"

# Same as run.ps1: without JAVA_HOME use the kit JRE (this process only).
_KIT_JRE = ROOT / "tools" / "jre"
if not os.environ.get("JAVA_HOME") and (_KIT_JRE / "bin" / "java.exe").exists():
    os.environ["JAVA_HOME"] = str(_KIT_JRE)
os.environ.pop("SPARK_HOME", None)


@pytest.fixture(scope="session")
def spark():
    from app.spark.session import get_spark, stop_spark

    session = get_spark(master="local[1]", extra_conf={"spark.sql.shuffle.partitions": "1"})
    yield session
    stop_spark()


@pytest.fixture
def data_dir():
    return DATA


@pytest.fixture(scope="session")
def _sessions():
    return {}


@pytest.fixture
def lab(spark, _sessions):
    """lab('stocks_small.csv') -> LabSession (loaded once; the `dataset` view is re-registered)."""
    import config
    from app.session import LabSession
    from app.spark.loader import detect_format, load_dataset, resolve_path, sniff_csv

    def get(name):
        if name not in _sessions:
            path = resolve_path(str(DATA / name))
            fmt = detect_format(path)
            load = load_dataset(spark, path, fmt, sniff_csv(path) if fmt == "csv" else None)
            _sessions[name] = LabSession.start(spark, load)
        session = _sessions[name]
        session.load.df.createOrReplaceTempView(config.VIEW_NAME)
        session.history.clear()
        session.choices.clear()
        return session
    return get


def chooser(answers):
    """Non-interactive choice: answers[key] for each NeedsInput; fails on unexpected questions."""
    asked = []

    def choose(need):
        asked.append(need)
        if need.key not in answers:
            raise AssertionError(f"Unexpected question: {need.key} {need.message} {need.options}")
        return answers[need.key]
    choose.asked = asked
    return choose
