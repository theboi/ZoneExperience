"""Fail-closed OpenRouter transport for configured-key model decisions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from friendly_bot.hyperparameters import (
    OPENROUTER_MODEL,
    OPENROUTER_TIMEOUT_SECONDS,
    ROUTING_MAX_ATTEMPTS,
)
from friendly_bot.routing.contracts import (
    KeySelectionRequest,
    MatchRankingRequest,
    PersonaSummaryRequest,
)

_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION = (
    "disabled-globally-or-friendly-bot-key-excluded"
)
type OpenRouterInputOutputLoggingAttestation = Literal[
    False,
    "disabled-globally-or-friendly-bot-key-excluded",
]


class GatewayError(RuntimeError):
    """A closed failure at the external model boundary."""


class GatewayPrivacyConfigurationError(GatewayError):
    """The local runtime lacks the required OpenRouter privacy attestation."""


class GatewayTransportError(GatewayError):
    """The compliant endpoint did not return a successful response in time."""


class GatewayProtocolError(GatewayError):
    """The provider response cannot safely choose a configured key."""


class OpenRouterSettings(BaseSettings):
    """Read the provider credential and non-secret privacy attestation."""

    model_config = SettingsConfigDict(env_prefix="", extra="forbid")

    openrouter_api_key: SecretStr | None = None
    friendly_bot_openrouter_input_output_logging_attestation: OpenRouterInputOutputLoggingAttestation = False

    @classmethod
    def from_environment(cls) -> OpenRouterSettings:
        """Load R03's environment-only configuration without surfacing values."""

        try:
            return cls()
        except ValidationError:
            raise GatewayPrivacyConfigurationError(
                "OpenRouter configuration is invalid"
            ) from None


class GatewayResponse(Protocol):
    """A response that returns parsed JSON and then discards provider data."""

    status_code: int

    def json(self) -> object: ...

    def discard(self) -> None: ...


class GatewayHttpClient(Protocol):
    """Injectable asynchronous HTTP boundary; production uses the stdlib client."""

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
    ) -> GatewayResponse: ...


class _StdlibResponse:
    def __init__(self, status_code: int, body: bytes | None = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        body = self._body
        self._body = None
        if body is None:
            raise ValueError("OpenRouter response body is unavailable")
        return json.loads(body.decode("utf-8"))

    def discard(self) -> None:
        self._body = None


class _StdlibAsyncHttpClient:
    """Small async wrapper that avoids a second application persistence boundary."""

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
    ) -> GatewayResponse:
        return await asyncio.to_thread(
            self._post_sync,
            url,
            headers=headers,
            payload=json,
            timeout=timeout,
        )

    @staticmethod
    def _post_sync(
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
    ) -> GatewayResponse:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return _StdlibResponse(response.status, response.read())
        except HTTPError as error:
            return _StdlibResponse(error.code)
        except URLError:
            raise GatewayTransportError("OpenRouter transport failed") from None


class OpenRouterGateway:
    """Select exactly one allowed key without ever producing user-facing prose."""

    def __init__(
        self,
        *,
        api_key: str | SecretStr,
        client: GatewayHttpClient | None = None,
        timeout_seconds: float = OPENROUTER_TIMEOUT_SECONDS,
        max_attempts: int = ROUTING_MAX_ATTEMPTS,
        input_output_logging_attestation: OpenRouterInputOutputLoggingAttestation = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if input_output_logging_attestation != (
            OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION
        ):
            raise GatewayPrivacyConfigurationError(
                "OpenRouter Input & Output Logging attestation is required"
            )
        self._api_key = (
            api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        )
        self._client = client or _StdlibAsyncHttpClient()
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts

    @classmethod
    def from_environment(cls) -> OpenRouterGateway:
        """Construct only when an operator explicitly attests provider logging safety."""

        settings = OpenRouterSettings.from_environment()
        if settings.openrouter_api_key is None:
            raise GatewayPrivacyConfigurationError(
                "OpenRouter configuration is invalid"
            )
        return cls(
            api_key=settings.openrouter_api_key,
            input_output_logging_attestation=(
                settings.friendly_bot_openrouter_input_output_logging_attestation
            ),
        )

    async def select_key(self, request: KeySelectionRequest) -> str:
        """Return one configured key or raise a closed gateway failure."""

        response = await self._post(
            self._payload(
                request,
                (
                    "Return exactly one JSON object with one key named 'key'. "
                    "Its value must be one of allowed_keys. Return no prose."
                ),
            )
        )
        return self._parse_selected_key(response, request.allowed_keys)

    async def summarize_persona(self, request: PersonaSummaryRequest) -> str:
        """Return a nonempty private persona summary or fail without a fallback."""

        response = await self._post(
            self._payload(
                request,
                "Return a concise persona summary. Return no structured identifiers.",
            )
        )
        content = self._assistant_content(response)
        summary = content.strip() if content is not None else ""
        content = None
        if not summary:
            raise GatewayProtocolError("OpenRouter returned an empty persona summary")
        return summary

    async def rank_aliases(self, request: MatchRankingRequest) -> tuple[str, ...]:
        """Rank safe local aliases without exposing durable profile identifiers."""

        remaining = {candidate.alias: candidate for candidate in request.candidates}
        if len(remaining) != len(request.candidates):
            raise GatewayProtocolError("match aliases must be distinct")
        ranked: list[str] = []
        while remaining:
            response = await self._post(
                self._payload(
                    MatchRankingRequest(candidates=tuple(remaining.values())),
                    (
                        "Return exactly one JSON object with one key named 'key'. "
                        "Its value must be an available alias or system.done. Return no prose."
                    ),
                )
            )
            key = self._parse_selected_key(
                response, frozenset(remaining) | {"system.done"}
            )
            if key == "system.done":
                break
            ranked.append(key)
            del remaining[key]
        return tuple(ranked + list(remaining))

    def _payload(
        self,
        request: KeySelectionRequest | PersonaSummaryRequest | MatchRankingRequest,
        instruction: str,
    ) -> dict[str, object]:
        return {
            "model": OPENROUTER_MODEL,
            "provider": {"zdr": True, "data_collection": "deny"},
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(request.model_dump(mode="json")),
                },
            ],
        }

    async def _post(self, payload: Mapping[str, object]) -> GatewayResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        for attempt in range(self._max_attempts):
            transport_failed = False
            try:
                response = await self._client.post(
                    _CHAT_COMPLETIONS_URL,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout_seconds,
                )
                if 200 <= response.status_code < 300:
                    return response
                response.discard()
            except Exception:  # noqa: BLE001 - provider errors can retain response bytes
                response = None
                transport_failed = True
            else:
                response = None
                transport_failed = True
            if transport_failed:
                if attempt + 1 == self._max_attempts:
                    break
                await asyncio.sleep(0)
                continue
        raise GatewayTransportError("OpenRouter request exhausted")

    @staticmethod
    def _parse_selected_key(
        response: GatewayResponse, allowed_keys: frozenset[str]
    ) -> str:
        content = OpenRouterGateway._assistant_content(response)
        parsed: object | None = None
        candidate: object | None = None
        selected_key: str | None = None
        error_message = "OpenRouter response was not a key selection"
        if content is not None:
            try:
                parsed = json.loads(content)
            except (TypeError, ValueError):
                pass
            else:
                if isinstance(parsed, dict) and set(parsed) == {"key"}:
                    candidate = parsed["key"]
                    if isinstance(candidate, str) and candidate in allowed_keys:
                        selected_key = candidate
                    else:
                        error_message = "OpenRouter selected an invalid key"
                else:
                    error_message = "OpenRouter selected an invalid key"
        content = None
        parsed = None
        candidate = None
        if selected_key is None:
            raise GatewayProtocolError(error_message)
        return selected_key

    @staticmethod
    def _assistant_content(response: GatewayResponse) -> str | None:
        payload = OpenRouterGateway._request_json(response)
        if payload is None:
            return None
        try:
            choices = payload["choices"]
            if not isinstance(choices, list):
                raise TypeError("choices must be a list")
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise TypeError("choice must be an object")
            message = choice["message"]
            if not isinstance(message, Mapping):
                raise TypeError("message must be an object")
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError("content must be text")
            return content
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _request_json(response: GatewayResponse) -> Mapping[str, object] | None:
        """Parse one provider envelope without retaining malformed raw response text."""

        parse_failed = False
        payload: object | None = None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - provider errors can retain response bytes
            parse_failed = True
        try:
            response.discard()
        except Exception:  # noqa: BLE001 - provider errors can retain response bytes
            parse_failed = True
        if parse_failed or not isinstance(payload, Mapping):
            return None
        return payload
