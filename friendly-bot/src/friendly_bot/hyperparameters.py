"""Non-secret tunables for private routing and persona maintenance."""

from __future__ import annotations

from datetime import timedelta

OPENROUTER_MODEL = "qwen/qwen3.7-flash"
OPENROUTER_TIMEOUT_SECONDS = 10.0
OPENROUTER_MAX_RESPONSE_BYTES = 64 * 1024
ROUTING_MAX_ATTEMPTS = 3
PERSONA_IDLE_AFTER = timedelta(hours=48)
PERSONA_MAX_UNSUMMARIZED_TOKENS = 512
