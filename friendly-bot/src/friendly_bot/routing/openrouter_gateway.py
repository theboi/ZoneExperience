"""Fail-closed OpenRouter transport for configured-key model decisions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from friendly_bot.hyperparameters import (
    OPENROUTER_MAX_RESPONSE_BYTES,
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


@dataclass(frozen=True, slots=True)
class _DecodedProviderValue:
    """A provider result already validated for its intended gateway operation."""

    value: str


@dataclass(frozen=True, slots=True)
class _ProviderProtocolFailure:
    """A fixed, non-provider-derived protocol failure summary."""

    message: str


@dataclass(frozen=True, slots=True)
class _ProviderTransportFailure:
    """A status-only or ordinary transport failure summary."""


type _DecodedProviderResult = (
    _DecodedProviderValue | _ProviderProtocolFailure | _ProviderTransportFailure
)
type _ProviderPayloadDecoder = Callable[[object], _DecodedProviderResult]


class GatewayHttpClient(Protocol):
    """Return only decoded values or typed failures from the provider boundary."""

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
        decoder: _ProviderPayloadDecoder,
    ) -> _DecodedProviderResult: ...


class _BoundedReadableResponse(Protocol):
    """The only successful stdlib response capability the decoder needs."""

    def read(self, amount: int | None = None) -> bytes: ...


class _StdlibAsyncHttpClient:
    """Small async wrapper that avoids a second application persistence boundary."""

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
        decoder: _ProviderPayloadDecoder,
    ) -> _DecodedProviderResult:
        return await asyncio.to_thread(
            self._post_sync,
            url,
            headers=headers,
            payload=json,
            timeout=timeout,
            decoder=decoder,
        )

    @staticmethod
    def _post_sync(
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float,
        decoder: _ProviderPayloadDecoder,
    ) -> _DecodedProviderResult:
        request_body: bytes | None = None
        request_headers = dict(headers)
        request: Request | None = None
        try:
            request_body = json.dumps(payload).encode("utf-8")
            request = Request(
                url,
                data=request_body,
                headers=request_headers,
                method="POST",
            )
            with urlopen(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    return _ProviderTransportFailure()
                return _decode_stdlib_success_response(response, decoder)
        except HTTPError:
            # Deliberately do not read an HTTPError body: it may contain provider data.
            return _ProviderTransportFailure()
        except Exception:  # noqa: BLE001 - ordinary client failures are retryable
            # Ordinary client failures (including interrupted reads) are retryable.
            return _ProviderTransportFailure()
        finally:
            request = None
            request_body = None
            request_headers.clear()


def _decode_stdlib_success_response(
    response: _BoundedReadableResponse, decoder: _ProviderPayloadDecoder
) -> _DecodedProviderResult:
    """Bound, parse, validate, and clear one successful provider body in one scope."""

    body = bytearray()
    try:
        try:
            body.extend(response.read(OPENROUTER_MAX_RESPONSE_BYTES + 1))
        except Exception:  # noqa: BLE001 - provider exceptions can retain body bytes
            return _ProviderTransportFailure()
        if len(body) > OPENROUTER_MAX_RESPONSE_BYTES:
            return _ProviderProtocolFailure(
                "OpenRouter response exceeded the body limit"
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001 - malformed/deep provider JSON is closed here
            return _ProviderProtocolFailure("OpenRouter response was not valid JSON")
        try:
            result = decoder(payload)
        except Exception:  # noqa: BLE001 - decoder failures are protocol failures
            return _ProviderProtocolFailure("OpenRouter response was invalid")
        if isinstance(
            result,
            (
                _DecodedProviderValue,
                _ProviderProtocolFailure,
                _ProviderTransportFailure,
            ),
        ):
            return result
        return _ProviderProtocolFailure("OpenRouter response was invalid")
    finally:
        body.clear()


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

        result = await self._post(
            self._payload(
                request,
                (
                    "Return exactly one JSON object with one key named 'key'. "
                    "Its value must be one of allowed_keys. Return no prose."
                ),
            ),
            self._key_decoder(request.allowed_keys),
        )
        return self._value_or_raise(result)

    async def summarize_persona(self, request: PersonaSummaryRequest) -> str:
        """Return a nonempty private persona summary or fail without a fallback."""

        result = await self._post(
            self._payload(
                request,
                "Return a concise persona summary. Return no structured identifiers.",
            ),
            self._decode_persona_summary,
        )
        return self._value_or_raise(result)

    async def rank_aliases(self, request: MatchRankingRequest) -> tuple[str, ...]:
        """Rank safe local aliases without exposing durable profile identifiers."""

        remaining = {candidate.alias: candidate for candidate in request.candidates}
        if len(remaining) != len(request.candidates):
            raise GatewayProtocolError("match aliases must be distinct")
        ranked: list[str] = []
        while remaining:
            result = await self._post(
                self._payload(
                    MatchRankingRequest(candidates=tuple(remaining.values())),
                    (
                        "Return exactly one JSON object with one key named 'key'. "
                        "Its value must be an available alias or system.done. Return no prose."
                    ),
                ),
                self._key_decoder(frozenset(remaining) | {"system.done"}),
            )
            key = self._value_or_raise(result)
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

    async def _post(
        self, payload: Mapping[str, object], decoder: _ProviderPayloadDecoder
    ) -> _DecodedProviderResult:
        """Retry only safe decoded outcomes; raw responses do not leave this boundary."""

        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        request_payload = payload
        del payload
        result: _DecodedProviderResult = _ProviderTransportFailure()
        try:
            for attempt in range(self._max_attempts):
                try:
                    result = await self._client.post(
                        _CHAT_COMPLETIONS_URL,
                        headers=headers,
                        json=request_payload,
                        timeout=self._timeout_seconds,
                        decoder=decoder,
                    )
                except Exception:  # noqa: BLE001 - provider exceptions can retain bytes
                    result = _ProviderTransportFailure()
                if not isinstance(
                    result,
                    (
                        _DecodedProviderValue,
                        _ProviderProtocolFailure,
                        _ProviderTransportFailure,
                    ),
                ):
                    result = _ProviderTransportFailure()
                if not isinstance(result, _ProviderTransportFailure):
                    return result
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(0)
            return _ProviderTransportFailure()
        finally:
            headers.clear()
            del request_payload

    @staticmethod
    def _value_or_raise(result: _DecodedProviderResult) -> str:
        """Raise only after the response lifecycle returned a typed safe outcome."""

        if isinstance(result, _DecodedProviderValue):
            return result.value
        if isinstance(result, _ProviderProtocolFailure):
            raise GatewayProtocolError(result.message)
        raise GatewayTransportError("OpenRouter request exhausted")

    @staticmethod
    def _key_decoder(allowed_keys: frozenset[str]) -> _ProviderPayloadDecoder:
        def decode(payload: object) -> _DecodedProviderResult:
            content = OpenRouterGateway._assistant_content(payload)
            if content is None:
                return _ProviderProtocolFailure(
                    "OpenRouter response was not a key selection"
                )
            try:
                parsed = json.loads(content)
            except Exception:  # noqa: BLE001 - malformed/deep content is protocol only
                return _ProviderProtocolFailure(
                    "OpenRouter response was not a key selection"
                )
            if not isinstance(parsed, dict) or set(parsed) != {"key"}:
                return _ProviderProtocolFailure("OpenRouter selected an invalid key")
            key = parsed["key"]
            if not isinstance(key, str) or key not in allowed_keys:
                return _ProviderProtocolFailure("OpenRouter selected an invalid key")
            return _DecodedProviderValue(key)

        return decode

    @staticmethod
    def _decode_persona_summary(payload: object) -> _DecodedProviderResult:
        content = OpenRouterGateway._assistant_content(payload)
        if content is None:
            return _ProviderProtocolFailure(
                "OpenRouter returned an empty persona summary"
            )
        summary = content.strip()
        if not summary:
            return _ProviderProtocolFailure(
                "OpenRouter returned an empty persona summary"
            )
        return _DecodedProviderValue(summary)

    @staticmethod
    def _assistant_content(payload: object) -> str | None:
        try:
            if not isinstance(payload, Mapping):
                return None
            choices = payload["choices"]
            if not isinstance(choices, list):
                return None
            choice = choices[0]
            if not isinstance(choice, Mapping):
                return None
            message = choice["message"]
            if not isinstance(message, Mapping):
                return None
            content = message["content"]
            if not isinstance(content, str):
                return None
            return content
        except Exception:  # noqa: BLE001 - untrusted provider mappings must not escape
            return None
