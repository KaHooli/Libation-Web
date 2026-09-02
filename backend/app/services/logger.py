import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import settings

# Derived from LIBATION_CONFIG rather than hardcoded to /config: the deployed
# container mounts its volume there, but a run outside Docker points the setting
# elsewhere and cannot create a top-level directory.
_LOG_DIR = Path(settings.LIBATION_CONFIG) / "logs"
_LOG_FILE = _LOG_DIR / "libation-web.log"
_MAX_OUTPUT_CHARS = 20_000  # truncate very long CLI output in log entries

_logger: logging.Logger | None = None


def log_file_path() -> Path:
    """The file get_logger() writes to — so readers can't name a different one."""
    return _LOG_FILE



def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("libation")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)

    _logger = logger
    return logger


def log_cli(command: str, returncode: int, output: str, elapsed: float) -> None:
    logger = get_logger()
    status = "OK" if returncode == 0 else f"EXIT {returncode}"
    logger.info("[cli] %s → %s (%.1fs)", command, status, elapsed)
    if output:
        body = output if len(output) <= _MAX_OUTPUT_CHARS else output[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"
        logger.debug("[cli] output:\n%s", body)
