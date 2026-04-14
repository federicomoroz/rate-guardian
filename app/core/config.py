import os

REDIS_URL                       = os.getenv("REDIS_URL", "redis://localhost:6379")
CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
CIRCUIT_BREAKER_RECOVERY_SECONDS  = int(os.getenv("CIRCUIT_BREAKER_RECOVERY_SECONDS", "30"))
LOG_RETENTION_DAYS              = int(os.getenv("LOG_RETENTION_DAYS", "7"))
