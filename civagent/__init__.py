from civsim import logger
from civagent.utils.llm_router import redact

# Scrub API-key-looking secrets (sk-..., Bearer ...) from every log record (EXECUTION.md W1b.7).
try:
    logger.configure(patcher=lambda record: record.update(message=redact(record["message"])))
except Exception:  # pragma: no cover - logger setup must never break imports
    pass

logger = logger
default_from_name = "player"
default_gameid = "aa9092fe-61fb-4554-8102-d77d5c689851"
