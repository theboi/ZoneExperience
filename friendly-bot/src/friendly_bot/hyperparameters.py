"""Non-secret tunables for private routing and persona maintenance."""

from __future__ import annotations

from datetime import timedelta

OPENROUTER_MODEL = "mistralai/mistral-nemo"
OPENROUTER_TIMEOUT_SECONDS = 10.0
OPENROUTER_MAX_RESPONSE_BYTES = 64 * 1024
ROUTING_MAX_ATTEMPTS = 3
OPENROUTER_HTTP_MAX_ATTEMPTS = 2
PERSONA_IDLE_AFTER = timedelta(hours=48)
PERSONA_MAX_UNSUMMARIZED_TOKENS = 512
