"""Fail-closed OpenRouter transport for configured-key model decisions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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

    model_config = SettingsConfigDict(env_prefix="", extra="forbid", env_file=".env")

    openrouter_api_key: SecretStr | None = None
    friendly_bot_openrouter_input_output_logging_attestation: OpenRouterInputOutputLoggingAttestation = False

    @classmethod
    def from_environment(cls) -> OpenRouterSettings:
        """Load R03's environment-only configuration without surfacing values."""

        try:
            return cls()
        except ValidationError:
            pass
        raise GatewayPrivacyConfigurationError("OpenRouter configuration is invalid")


@dataclass(frozen=True, slots=True)
class _DecodedProviderValue:
    """A provider result already validated for its intended gateway operation."""

    value: str
    capability: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _ProviderProtocolFailure:
    """A fixed, non-provider-derived protocol failure summary."""

    capability: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _ProviderTransportFailure:
    """A status-only or ordinary transport failure summary."""


type _DecodedProviderResult = (
    _DecodedProviderValue | _ProviderProtocolFailure | _ProviderTransportFailure
)


@dataclass(frozen=True, slots=True)
class _ProviderPayloadDecoder:
    """Tag results so injected clients cannot forge a decoder outcome."""

    capability: object
    decode: Callable[[object, object], _DecodedProviderResult]

    def __call__(self, payload: object) -> _DecodedProviderResult:
        return self.decode(payload, self.capability)


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
        header_snapshot = dict(headers)
        payload_snapshot = dict(json)
        return await asyncio.to_thread(
            self._post_sync,
            url,
            headers=header_snapshot,
            payload=payload_snapshot,
            timeout=timeout,
            decoder=decoder,
        )

    @staticmethod
    def _post_sync(
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
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
            headers.clear()
            payload.clear()


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
            return _ProviderProtocolFailure(decoder.capability)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001 - malformed/deep provider JSON is closed here
            return _ProviderProtocolFailure(decoder.capability)
        try:
            result = decoder(payload)
        except Exception:  # noqa: BLE001 - decoder failures are protocol failures
            return _ProviderProtocolFailure(decoder.capability)
        if isinstance(
            result,
            (
                _DecodedProviderValue,
                _ProviderProtocolFailure,
                _ProviderTransportFailure,
            ),
        ):
            return result
        return _ProviderProtocolFailure(decoder.capability)
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

        return self._key_value_or_raise(
            await self._post(
                self._payload(
                    request,
                    (
                        "Return exactly one JSON object with one key named 'key'. "
                        "Its value must be one of allowed_keys. Return no prose."
                    ),
                ),
                self._key_decoder(request.allowed_keys),
            ),
            request.allowed_keys,
        )

    async def summarize_persona(self, request: PersonaSummaryRequest) -> str:
        """Return a nonempty private persona summary or fail without a fallback."""

        return self._persona_value_or_raise(
            await self._post(
                self._payload(
                    request,
                    "Return a concise persona summary. Return no structured identifiers.",
                ),
                self._persona_decoder(),
            )
        )

    async def rank_aliases(self, request: MatchRankingRequest) -> tuple[str, ...]:
        """Rank safe local aliases without exposing durable profile identifiers."""

        remaining = {candidate.alias: candidate for candidate in request.candidates}
        if len(remaining) != len(request.candidates):
            raise GatewayProtocolError("match aliases must be distinct")
        ranked: list[str] = []
        while remaining:
            allowed_keys = frozenset(remaining) | {"system.done"}
            key = self._key_value_or_raise(
                await self._post(
                    self._payload(
                        MatchRankingRequest(candidates=tuple(remaining.values())),
                        (
                            "Return exactly one JSON object with one key named 'key'. "
                            "Its value must be an available alias or system.done. Return no prose."
                        ),
                    ),
                    self._key_decoder(allowed_keys),
                ),
                allowed_keys,
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

    async def _post(
        self, payload: dict[str, object], decoder: _ProviderPayloadDecoder
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
                if not self._is_current_decoder_result(result, decoder):
                    result = _ProviderTransportFailure()
                if not isinstance(result, _ProviderTransportFailure):
                    return result
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(0)
            return _ProviderTransportFailure()
        finally:
            headers.clear()
            request_payload.clear()

    @staticmethod
    def _is_current_decoder_result(
        result: object, decoder: _ProviderPayloadDecoder
    ) -> bool:
        if isinstance(result, _ProviderTransportFailure):
            return True
        if isinstance(result, (_DecodedProviderValue, _ProviderProtocolFailure)):
            return result.capability is decoder.capability
        return False

    @staticmethod
    def _key_value_or_raise(
        result: _DecodedProviderResult, allowed_keys: frozenset[str]
    ) -> str:
        """Revalidate a decoded key and drop the result before raising any error."""

        transport_failed = isinstance(result, _ProviderTransportFailure)
        value: object | None = None
        if isinstance(result, _DecodedProviderValue):
            value = result.value
        del result
        if transport_failed:
            raise GatewayTransportError("OpenRouter request exhausted")
        if isinstance(value, str) and value in allowed_keys:
            return value
        value = None
        raise GatewayProtocolError("OpenRouter response was not a key selection")

    @staticmethod
    def _persona_value_or_raise(result: _DecodedProviderResult) -> str:
        """Revalidate a summary and drop an invalid result before raising an error."""

        transport_failed = isinstance(result, _ProviderTransportFailure)
        summary: object | None = None
        if isinstance(result, _DecodedProviderValue):
            summary = result.value
        del result
        if transport_failed:
            raise GatewayTransportError("OpenRouter request exhausted")
        if isinstance(summary, str):
            validated_summary = summary.strip()
            summary = None
            if validated_summary:
                return validated_summary
        summary = None
        raise GatewayProtocolError("OpenRouter returned an empty persona summary")

    @staticmethod
    def _key_decoder(allowed_keys: frozenset[str]) -> _ProviderPayloadDecoder:
        capability = object()

        def decode(payload: object, capability: object) -> _DecodedProviderResult:
            content = OpenRouterGateway._assistant_content(payload)
            if content is None:
                return _ProviderProtocolFailure(capability)
            try:
                parsed = json.loads(content)
            except Exception:  # noqa: BLE001 - malformed/deep content is protocol only
                return _ProviderProtocolFailure(capability)
            if not isinstance(parsed, dict) or set(parsed) != {"key"}:
                return _ProviderProtocolFailure(capability)
            key = parsed["key"]
            if not isinstance(key, str) or key not in allowed_keys:
                return _ProviderProtocolFailure(capability)
            return _DecodedProviderValue(key, capability)

        return _ProviderPayloadDecoder(capability, decode)

    @staticmethod
    def _persona_decoder() -> _ProviderPayloadDecoder:
        capability = object()

        def decode(payload: object, capability: object) -> _DecodedProviderResult:
            content = OpenRouterGateway._assistant_content(payload)
            if content is None:
                return _ProviderProtocolFailure(capability)
            summary = content.strip()
            if not summary:
                return _ProviderProtocolFailure(capability)
            return _DecodedProviderValue(summary, capability)

        return _ProviderPayloadDecoder(capability, decode)

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
