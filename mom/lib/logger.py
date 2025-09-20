import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
_BACKUPS = 3


def _build_handler(log_path: Path, fmt: str) -> logging.Handler:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS)
    h.setFormatter(logging.Formatter(fmt))
    h.setLevel(logging.DEBUG)
    return h


def get_logger(
    name: str,
    *,
    fmt: str = _DEFAULT_FMT,
    level: int | str | None = None,
) -> logging.Logger:
    """
    Module-level helper.  Call **once** per module:

        log = get_logger(__name__)

    `log_file` defaults to `c_env.LOG_FILE` or `logs/commune.log`.
    """
    import commune.config as _cfg

    path = _cfg.c_env.LOG_FILE
    lvl = level or getattr(_cfg.c_env, "LOG_LEVEL", "INFO")

    logger = logging.getLogger(name)
    if not logger.handlers:  # prevent double-handlers
        logger.setLevel(lvl)
        logger.addHandler(_build_handler(path, fmt))
        logger.addHandler(logging.StreamHandler())  # still echo to stderr
        logger.propagate = False
    return logger
