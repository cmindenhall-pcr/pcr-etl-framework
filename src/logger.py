import logging
import sys
from pathlib import Path


def get_logger(name: str = "data_cockpit"):
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        file_handler = logging.FileHandler(log_dir / "data_cockpit.log")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if not any(
        isinstance(handler, logging.StreamHandler)
        and getattr(handler, "stream", None) is sys.stdout
        for handler in logger.handlers
    ):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger.propagate = False

    return logger
