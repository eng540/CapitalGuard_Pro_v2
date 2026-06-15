import logging, sys, os

def setup_logging():
    logger = logging.getLogger("capitalguard")
    if logger.handlers:
        return logger
    level = logging.INFO if os.getenv("ENV","dev")!="dev" else logging.DEBUG
    handler = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger