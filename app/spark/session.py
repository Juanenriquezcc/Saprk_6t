"""Single SparkSession built from the central configuration."""
import atexit
import logging
import os
import subprocess
import sys
from contextlib import contextmanager

import config
from app.errors import AppError, short_error

_spark = None


def get_spark(master=None, extra_conf=None, quiet=True):
    """quiet=True sends the JVM startup stderr to config.JVM_LOG_FILE (clean console)."""
    global _spark
    if _spark is not None:
        return _spark

    # Python workers must use this same interpreter (the one in .venv).
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise AppError("PySpark no esta instalado en este entorno.",
                       "Ejecute run.cmd: prepara el entorno con el kit de la USB.") from exc

    builder = SparkSession.builder.appName(config.SPARK_APP_NAME).master(master or config.SPARK_MASTER)
    for key, value in {**config.SPARK_CONF, **(extra_conf or {})}.items():
        builder = builder.config(key, value)

    try:
        with _jvm_stderr_to_log(quiet):
            _spark = builder.getOrCreate()
    except Exception as exc:
        hint = _startup_hint(exc) + (f" Log de la JVM: {config.JVM_LOG_FILE}" if quiet else "")
        raise AppError("No se pudo iniciar Spark: " + short_error(exc), hint) from exc
    _spark.sparkContext.setLogLevel(config.SPARK_LOG_LEVEL)
    if quiet:
        _silence_sql_error_log()
    if os.name == "nt":
        # Registered AFTER PySpark's own atexit hook, so it runs before it.
        atexit.register(_silence_std_streams)
    return _spark


@contextmanager
def _jvm_stderr_to_log(enabled):
    """The JVM inherits the process stderr at startup and prints warnings there that
    cannot be turned off (e.g. 'Using incubator modules'). While it starts, stderr
    points to the log file; afterwards Python gets its normal stderr back."""
    if not enabled:
        yield
        return
    config.JVM_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr.flush()
    saved = os.dup(2)
    log_fd = os.open(config.JVM_LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.dup2(log_fd, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(log_fd)


def _silence_sql_error_log():
    """PySpark 4 logs every failed query as a long JSON record on stderr. The user already
    gets a clear message (executor.translate_error), so the record is hidden (--debug shows it).
    It must be a PySparkLogger: PySpark calls it with extra keyword arguments."""
    try:
        from pyspark.logger import PySparkLogger
    except ImportError:
        return
    PySparkLogger.getLogger("SQLQueryContextLogger").setLevel(logging.CRITICAL)


def _silence_std_streams():
    """On exit PySpark (Windows) runs 'taskkill' to stop the JVM, which prints
    'SUCCESS: the process ... has been terminated'. Output goes to NUL only at the very end."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    except OSError:
        pass


def stop_spark():
    global _spark
    if _spark is not None:
        _spark.stop()
        _spark = None
        _shutdown_jvm()


def _shutdown_jvm():
    """Ends the JVM now and waits for it. PySpark only kills it in an atexit hook with an
    asynchronous taskkill, so the JVM could outlive the program for a moment, keep the
    .venv jars locked (a --rebuild-venv right after would fail) and use RAM."""
    from pyspark import SparkContext

    gateway = SparkContext._gateway
    if gateway is None:
        return
    proc = getattr(gateway, "proc", None)
    try:
        gateway.shutdown()
    except Exception:
        pass
    if proc is not None and proc.poll() is None:
        if os.name == "nt":   # spark-submit.cmd starts the JVM as a child: kill the whole tree
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    SparkContext._gateway = None
    SparkContext._jvm = None


def _startup_hint(exc):
    text = str(exc)
    if "JAVA_GATEWAY_EXITED" in text or "JAVA_HOME" in text or not os.environ.get("JAVA_HOME"):
        versions = "/".join(map(str, config.SUPPORTED_JAVA))
        return (f"Spark necesita Java {versions}. Inicie la aplicacion con run.cmd, "
                "que configura JAVA_HOME (solo para el proceso) con el JRE del kit.")
    return "Ejecute 'run.cmd -CheckOnly' para diagnosticar el entorno."
