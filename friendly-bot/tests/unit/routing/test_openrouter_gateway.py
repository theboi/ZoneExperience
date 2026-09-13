"""Behavioral coverage for the private OpenRouter boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from http.client import IncompleteRead
from io import BytesIO
from typing import Any, Self
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from friendly_bot.hyperparameters import (
    OPENROUTER_MAX_RESPONSE_BYTES,
    ROUTING_MAX_ATTEMPTS,
)
from friendly_bot.routing.contracts import (
    KeySelectionRequest,
    MatchPromptCandidate,
    MatchRankingRequest,
    PersonaSummaryRequest,
    RoutingPromptCandidate,
)
from friendly_bot.routing.openrouter_gateway import (
    GatewayError,
    GatewayProtocolError,
    GatewayTransportError,
    OpenRouterGateway,
    _DecodedProviderValue,
    _ProviderProtocolFailure,
)

_OBSERVABILITY_ATTESTATION = "disabled-globally-or-friendly-bot-key-excluded"
_IDENTIFIER_SENTINELS = (
    "telegram-user-id-sentinel-729",
    "telegram-chat-id-sentinel-418",
    "dob-sentinel-2001-02-03",
)


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return deepcopy(self.payload)

    def discard(self) -> None:
        self.payload.clear()

    def decode(self, decoder: Callable[[object], Any]) -> Any:
        try:
            if not 200 <= self.status_code < 300:
                raise OSError("non-success response")
            return decoder(deepcopy(self.payload))
        finally:
            self.discard()


@dataclass
class InvalidJsonEnvelopeResponse:
    raw_response: str
    status_code: int = 200

    def json(self) -> object:
        raise json.JSONDecodeError("invalid envelope", self.raw_response, 0)

    def discard(self) -> None:
        self.raw_response = ""

    def decode(self, decoder: Callable[[object], Any]) -> Any:
        self.discard()
        return decoder(None)


@dataclass
class IncompleteJsonResponse:
    raw_response: bytes
    status_code: int = 200

    def json(self) -> object:
        raise IncompleteRead(self.raw_response, expected=len(self.raw_response) + 1)

    def discard(self) -> None:
        self.raw_response = b""

    def decode(self, decoder: Callable[[object], Any]) -> Any:
        raise IncompleteRead(self.raw_response, expected=len(self.raw_response) + 1)


@dataclass
class DiscardFailingJsonResponse:
    """A provider response whose cleanup leaves its raw body in place."""

    raw_response: bytes
    status_code: int = 200

    def json(self) -> object:
        return {"choices": [{"message": {"content": '{"key":"flow.a"}'}}]}

    def discard(self) -> None:
        raise OSError("discard failed")

    def decode(self, decoder: Callable[[object], Any]) -> Any:
        raise OSError("discard failed")


@dataclass
class RawStdlibResponse:
    raw_body: bytes
    status: int = 200
    close_error: bool = False
    read_limits: list[int | None] = field(default_factory=list)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> bool:
        if self.close_error:
            raise OSError("discard failed")
        return False

    def read(self, amount: int | None = None) -> bytes:
        self.read_limits.append(amount)
        return self.raw_body if amount is None else self.raw_body[:amount]


@dataclass
class FakeHttpxClient:
    responses: list[
        FakeResponse
        | InvalidJsonEnvelopeResponse
        | IncompleteJsonResponse
        | DiscardFailingJsonResponse
        | Exception
    ]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def post(
        self, url: str, **kwargs: Any
    ) -> (
        FakeResponse
        | InvalidJsonEnvelopeResponse
        | IncompleteJsonResponse
        | DiscardFailingJsonResponse
    ):
        self.requests.append(
            {
                "url": url,
                "headers": dict(kwargs["headers"]),
                "json": deepcopy(kwargs["json"]),
                "timeout": kwargs["timeout"],
                "decoder": kwargs["decoder"],
            }
        )
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome.decode(kwargs["decoder"])


def _gateway(client: FakeHttpxClient) -> OpenRouterGateway:
    return OpenRouterGateway(
        api_key="test-only",
        client=client,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )


def _traceback_gateway_locals_hold_sentinel(
    error: BaseException, raw_sentinel: bytes
) -> bool:
    """Inspect only the raised error's gateway frames, never test-frame locals."""

    seen: set[int] = set()
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith(
            "friendly_bot/routing/openrouter_gateway.py"
        ) and _value_holds_sentinel(frame.f_locals, raw_sentinel, seen):
            return True
        traceback = traceback.tb_next
    return False


def _value_holds_sentinel(value: object, raw_sentinel: bytes, seen: set[int]) -> bool:
    """Traverse provider-response locals without searching the ambient test graph."""

    if isinstance(value, (bytes, bytearray)):
        return raw_sentinel in value
    if isinstance(value, str):
        return raw_sentinel.decode() in value
    if value is None or isinstance(value, (bool, float, int)):
        return False
    value_id = id(value)
    if value_id in seen:
        return False
    seen.add(value_id)
    if isinstance(value, dict):
        return any(
            _value_holds_sentinel(item, raw_sentinel, seen)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_value_holds_sentinel(item, raw_sentinel, seen) for item in value)
    try:
        attributes = vars(value)
    except TypeError:
        return False
    return _value_holds_sentinel(attributes, raw_sentinel, seen)


def _assert_closed_error_has_no_provider_bytes(
    error: GatewayError, raw_sentinel: bytes
) -> None:
    raw_text = raw_sentinel.decode()
    assert error.__cause__ is None
    assert error.__context__ is None
    assert raw_text not in str(error)
    assert all(raw_text not in str(argument) for argument in error.args)
    assert vars(error) == {}
    assert not _traceback_gateway_locals_hold_sentinel(error, raw_sentinel)


@pytest.mark.parametrize(
    "request_factory",
    [
        pytest.param(
            lambda field, value: KeySelectionRequest(
                allowed_keys={"flow.a"},
                messages=["telegram and dob are ordinary words"],
                **{field: value},
            ),
            id="key-selection",
        ),
        pytest.param(
            lambda field, value: PersonaSummaryRequest(
                messages=["telegram and dob are ordinary words"],
                **{field: value},
            ),
            id="persona-summary",
        ),
        pytest.param(
            lambda field, value: MatchRankingRequest(
                candidates=[MatchPromptCandidate(alias="candidate-0")],
                **{field: value},
            ),
            id="match-ranking",
        ),
        pytest.param(
            lambda field, value: RoutingPromptCandidate(
                key="flow.a",
                gist="telegram and dob are ordinary words",
                context_label="current",
                **{field: value},
            ),
            id="routing-candidate",
        ),
        pytest.param(
            lambda field, value: MatchPromptCandidate(
                alias="candidate-0",
                interests=["telegram and dob are ordinary words"],
                **{field: value},
            ),
            id="match-candidate",
        ),
    ],
)
@pytest.mark.parametrize(
    ("field", "sentinel"),
    [
        ("telegramUserId", _IDENTIFIER_SENTINELS[0]),
        ("telegram_chat_id", _IDENTIFIER_SENTINELS[1]),
        ("dob", _IDENTIFIER_SENTINELS[2]),
    ],
)
def test_every_prompt_dto_rejects_common_structured_identifier_aliases(
    request_factory: Any, field: str, sentinel: str
) -> None:
    """Allowing any identifier alias would let it cross the model boundary."""

    with pytest.raises(ValidationError):
        request_factory(field, sentinel)


def test_gateway_fails_closed_without_observability_attestation() -> None:
    """Constructing an executable gateway without the operator attestation is unsafe."""

    with pytest.raises(GatewayError, match="Input & Output Logging"):
        OpenRouterGateway(api_key="test-only", client=FakeHttpxClient([]))


@pytest.mark.parametrize(
    "attestation",
    [None, "true", "disabled-globally-or-friendly-bot-key-exclude"],
)
def test_gateway_environment_rejects_missing_or_inexact_observability_attestation(
    monkeypatch: pytest.MonkeyPatch, attestation: str | None
) -> None:
    """Only the exact non-secret operator attestation may enable a live gateway."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    if attestation is None:
        monkeypatch.delenv(
            "FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION", raising=False
        )
    else:
        monkeypatch.setenv(
            "FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION", attestation
        )

    with pytest.raises(GatewayError):
        OpenRouterGateway.from_environment()


@pytest.mark.parametrize(
    "forbidden",
    [
        {"telegram_user_id": "telegram-id-sentinel-729"},
        {"telegram_chat_id": "telegram-chat-sentinel-418"},
        {"dob": "dob-sentinel-2001-02-03"},
        {"source_message_id": "message-id-sentinel-11"},
    ],
)
def test_prompt_dto_forbids_structured_identity_fields(
    forbidden: dict[str, str],
) -> None:
    """Adding an identifier field must fail, rather than becoming prompt content."""

    with pytest.raises(ValidationError):
        KeySelectionRequest(
            allowed_keys={"flow.a"},
            user_name="A",
            messages=["telegram and dob are ordinary words"],
            **forbidden,
        )


async def test_request_requires_policy_and_excludes_identifier_sentinels() -> None:
    """A valid request carries policy controls but keeps structured identity out."""

    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": '{"key":"flow.a"}'}}]},
            )
        ]
    )
    gateway = _gateway(client)
    request = KeySelectionRequest(
        allowed_keys={"flow.a"},
        user_name="A",
        messages=["I can say telegram and dob in ordinary text"],
        candidates=[
            RoutingPromptCandidate(
                key="flow.a",
                gist="telegram and dob are ordinary words",
                context_label="current",
            )
        ],
    )

    assert await gateway.select_key(request) == "flow.a"

    payload = client.requests[0]["json"]
    serialized = json.dumps(payload)
    assert payload["model"] == "qwen/qwen3.7-flash"
    assert payload["provider"] == {"zdr": True, "data_collection": "deny"}
    assert set(payload) == {"model", "provider", "messages"}
    assert all(sentinel not in serialized for sentinel in _IDENTIFIER_SENTINELS)
    assert "telegram" in serialized
    assert "dob" in serialized


@pytest.mark.parametrize(
    "content",
    [
        '{"key":"flow.a","extra":"no"}',
        '{"key":"flow.unknown"}',
        '["flow.a"]',
        "not json",
    ],
)
async def test_gateway_fails_closed_for_non_single_whitelisted_key(
    content: str,
) -> None:
    """Relaxing exact-key parsing would turn model prose into routing authority."""

    client = FakeHttpxClient(
        [FakeResponse(200, {"choices": [{"message": {"content": content}}]})]
    )
    gateway = _gateway(client)

    with pytest.raises(GatewayProtocolError):
        await gateway.select_key(
            KeySelectionRequest(
                allowed_keys={"flow.a"}, user_name="A", messages=["hello"]
            )
        )


async def test_gateway_does_not_attach_raw_malformed_response_to_protocol_error() -> (
    None
):
    """Malformed provider text must not survive on the closed protocol error."""

    raw_response = "raw-provider-response-sentinel-not-json"
    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": raw_response}}]},
            )
        ]
    )

    with pytest.raises(GatewayProtocolError) as raised:
        await _gateway(client).select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert raw_response not in str(raised.value)
    assert all(raw_response not in str(argument) for argument in raised.value.args)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_does_not_attach_raw_malformed_envelope_to_any_operation(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
) -> None:
    """Invalid envelope JSON must not cross the shared response boundary."""

    raw_envelope = "raw-provider-envelope-sentinel-not-json"
    client = FakeHttpxClient([InvalidJsonEnvelopeResponse(raw_envelope)])

    with pytest.raises(GatewayProtocolError) as raised:
        await operation(_gateway(client))

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert raw_envelope not in str(raised.value)
    assert all(raw_envelope not in str(argument) for argument in raised.value.args)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_detaches_interrupted_stdlib_response_body_from_any_operation(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupted stdlib body reads must retry and not leak provider bytes."""

    raw_partial = b"raw-provider-incomplete-read-sentinel"
    expected_attempts = ROUTING_MAX_ATTEMPTS
    attempts = 0
    read_limits: list[int | None] = []

    class IncompleteReadResponse:
        status = 200

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exception_type: type[BaseException] | None,
            exception: BaseException | None,
            traceback: object,
        ) -> bool:
            return False

        def read(self, amount: int | None = None) -> bytes:
            read_limits.append(amount)
            raise IncompleteRead(raw_partial, expected=expected_attempts)

    def interrupted_urlopen(*args: object, **kwargs: object) -> IncompleteReadResponse:
        nonlocal attempts
        attempts += 1
        return IncompleteReadResponse()

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", interrupted_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError) as raised:
        await operation(gateway)

    assert attempts == expected_attempts
    assert read_limits == [OPENROUTER_MAX_RESPONSE_BYTES + 1] * expected_attempts
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_partial)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_does_not_retain_actual_malformed_stdlib_body_in_traceback(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed successful stdlib body must leave no raw bytes on protocol errors."""

    raw_body = b'{"choices":"raw-provider-malformed-200-sentinel"}'
    attempts = 0
    responses: list[RawStdlibResponse] = []

    def malformed_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        nonlocal attempts
        attempts += 1
        response = RawStdlibResponse(raw_body)
        responses.append(response)
        return response

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", malformed_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayProtocolError) as raised:
        await operation(gateway)

    assert attempts == 1
    assert responses[0].read_limits == [OPENROUTER_MAX_RESPONSE_BYTES + 1]
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_body)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_does_not_read_or_retain_actual_http_error_body_in_traceback(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-2xx HTTPError body is unused and must not cross the retry boundary."""

    raw_body = b"raw-provider-http-error-sentinel"
    attempts = 0

    class TrackingBytesIO(BytesIO):
        def __init__(self, initial_bytes: bytes) -> None:
            super().__init__(initial_bytes)
            self.read_calls = 0

        def read(self, size: int = -1) -> bytes:
            self.read_calls += 1
            return super().read(size)

    error_bodies: list[TrackingBytesIO] = []

    def unavailable_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        nonlocal attempts
        attempts += 1
        error_body = TrackingBytesIO(raw_body)
        error_bodies.append(error_body)
        raise HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            503,
            "unavailable",
            None,
            error_body,
        )

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", unavailable_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError) as raised:
        await operation(gateway)

    assert attempts == ROUTING_MAX_ATTEMPTS
    assert [body.read_calls for body in error_bodies] == [0] * ROUTING_MAX_ATTEMPTS
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_body)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_normalizes_incomplete_json_without_traceback_retention(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
) -> None:
    """An interrupted decoded response must become a detached transport error."""

    raw_body = b"raw-provider-json-incomplete-read-sentinel"
    client = FakeHttpxClient([IncompleteJsonResponse(raw_body)] * ROUTING_MAX_ATTEMPTS)

    with pytest.raises(GatewayTransportError) as raised:
        await operation(_gateway(client))

    assert len(client.requests) == ROUTING_MAX_ATTEMPTS
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_body)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_does_not_retain_response_when_discard_fails(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
) -> None:
    """A failed cleanup must not leave the raw response in an error frame."""

    raw_body = b"raw-provider-discard-failure-sentinel"
    client = FakeHttpxClient([DiscardFailingJsonResponse(raw_body)])

    with pytest.raises(GatewayTransportError) as raised:
        await operation(_gateway(client))

    _assert_closed_error_has_no_provider_bytes(raised.value, raw_body)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_closes_deep_stdlib_json_without_raw_traceback_retention(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep JSON recursion is a closed protocol result after the body scope exits."""

    raw_sentinel = b"raw-provider-deep-json-sentinel"
    raw_body = (
        b'["' + raw_sentinel + b'",' + (b"[" * 1200) + b"0" + (b"]" * 1200) + b"]"
    )
    attempts = 0

    def recursive_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        nonlocal attempts
        attempts += 1
        return RawStdlibResponse(raw_body)

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", recursive_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayProtocolError) as raised:
        await operation(gateway)

    assert attempts == 1
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_sentinel)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_closes_deep_assistant_json_without_raw_traceback_retention(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection JSON recursion is normalized after the stdlib body has been cleared."""

    raw_sentinel = "raw-provider-deep-assistant-sentinel"
    deep_content = '["' + raw_sentinel + '",' + ("[" * 1200) + "0" + ("]" * 1200) + "]"
    raw_body = json.dumps(
        {"choices": [{"message": {"content": deep_content}}]}
    ).encode()

    def recursive_content_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        return RawStdlibResponse(raw_body)

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", recursive_content_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayProtocolError) as raised:
        await operation(gateway)

    _assert_closed_error_has_no_provider_bytes(raised.value, raw_sentinel.encode())


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_closes_stdlib_discard_failure_without_raw_traceback(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close failure after a successful read is retried without returning a body."""

    raw_sentinel = b"raw-provider-discard-close-sentinel"
    raw_body = (
        b'{"choices":[{"message":{"content":"{\\"key\\":\\"flow.a\\"}"}}],'
        b'"ignored":"' + raw_sentinel + b'"}'
    )
    attempts = 0

    def discard_failing_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        nonlocal attempts
        attempts += 1
        return RawStdlibResponse(raw_body, close_error=True)

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", discard_failing_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError) as raised:
        await operation(gateway)

    assert attempts == ROUTING_MAX_ATTEMPTS
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_sentinel)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda gateway: gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            ),
            id="selection",
        ),
        pytest.param(
            lambda gateway: gateway.summarize_persona(
                PersonaSummaryRequest(messages=["hello"])
            ),
            id="persona",
        ),
        pytest.param(
            lambda gateway: gateway.rank_aliases(
                MatchRankingRequest(
                    candidates=[MatchPromptCandidate(alias="candidate-0")]
                )
            ),
            id="matching",
        ),
    ],
)
async def test_gateway_rejects_oversize_stdlib_body_after_one_bounded_read(
    operation: Callable[[OpenRouterGateway], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful bodies are read at most one byte beyond the configured limit."""

    raw_sentinel = b"raw-provider-oversize-sentinel"
    response = RawStdlibResponse(raw_sentinel + (b"x" * OPENROUTER_MAX_RESPONSE_BYTES))

    def oversized_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        return response

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", oversized_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayProtocolError) as raised:
        await operation(gateway)

    assert response.read_limits == [OPENROUTER_MAX_RESPONSE_BYTES + 1]
    _assert_closed_error_has_no_provider_bytes(raised.value, raw_sentinel)


async def test_gateway_propagates_cancellation_unchanged() -> None:
    """Cancellation is not an ordinary provider failure and must remain visible."""

    @dataclass
    class CancellingClient:
        calls: int = 0

        async def post(self, url: str, **kwargs: Any) -> Any:
            self.calls += 1
            raise asyncio.CancelledError()

    client = CancellingClient()
    gateway = OpenRouterGateway(
        api_key="test-only",
        client=client,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(asyncio.CancelledError):
        await gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    assert client.calls == 1


async def test_gateway_cancellation_keeps_stdlib_worker_request_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller cleanup cannot erase a request a cancelled stdlib worker still needs."""

    snapshots: list[tuple[dict[str, str], Mapping[str, object]]] = []
    started = asyncio.Event()

    async def suspended_to_thread(
        function: Callable[..., object], *args: object, **kwargs: object
    ) -> object:
        del function, args
        headers = kwargs["headers"]
        payload = kwargs["payload"]
        assert isinstance(headers, dict)
        assert isinstance(payload, dict)
        snapshots.append((headers, payload))
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled worker unexpectedly resumed")

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.asyncio.to_thread", suspended_to_thread
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )
    task = asyncio.create_task(
        gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )
    )

    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert snapshots == [
        (
            {"Authorization": "Bearer test-only", "Content-Type": "application/json"},
            {
                "model": "qwen/qwen3.7-flash",
                "provider": {"zdr": True, "data_collection": "deny"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return exactly one JSON object with one key named 'key'. "
                            "Its value must be one of allowed_keys. Return no prose."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            '{"allowed_keys": ["flow.a"], "user_name": "", '
                            '"persona": "", "messages": ["hello"], '
                            '"reply_body": null, "candidates": []}'
                        ),
                    },
                ],
            },
        )
    ]


async def test_gateway_rejects_forged_client_results_without_provider_text() -> None:
    """Injected clients cannot turn raw provider text into a gateway error or key."""

    @dataclass
    class OneShotResultClient:
        result: object

        async def post(self, url: str, **kwargs: Any) -> object:
            result = self.result
            self.result = None
            return result

    raw_sentinel = "raw-provider-forged-client-sentinel"

    class ForgedProtocolFailure(_ProviderProtocolFailure):
        pass

    forged_protocol_failure = ForgedProtocolFailure(object())
    forged_protocol_failure.__dict__["raw_provider_text"] = raw_sentinel
    protocol_client = OneShotResultClient(forged_protocol_failure)
    protocol_gateway = OpenRouterGateway(
        api_key="test-only",
        client=protocol_client,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError) as raised:
        await protocol_gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    _assert_closed_error_has_no_provider_bytes(raised.value, raw_sentinel.encode())

    value_client = OneShotResultClient(_DecodedProviderValue(raw_sentinel, object()))
    value_gateway = OpenRouterGateway(
        api_key="test-only",
        client=value_client,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError) as raised:
        await value_gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    _assert_closed_error_has_no_provider_bytes(raised.value, raw_sentinel.encode())


async def test_persona_summary_uses_the_same_private_provider_policy() -> None:
    """A separate summary path must not weaken OpenRouter privacy controls."""

    client = FakeHttpxClient(
        [FakeResponse(200, {"choices": [{"message": {"content": "calm"}}]})]
    )
    gateway = _gateway(client)

    summary = await gateway.summarize_persona(
        PersonaSummaryRequest(user_name="A", persona="old", messages=["new words"])
    )

    assert summary == "calm"
    assert client.requests[0]["json"]["provider"] == {
        "zdr": True,
        "data_collection": "deny",
    }


async def test_match_ranking_returns_only_local_aliases() -> None:
    """Ranking must use aliases; passing profile identifiers would disclose durable data."""

    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": '{"key":"candidate-1"}'}}]},
            ),
            FakeResponse(
                200, {"choices": [{"message": {"content": '{"key":"system.done"}'}}]}
            ),
        ]
    )
    gateway = _gateway(client)

    aliases = await gateway.rank_aliases(
        MatchRankingRequest(
            candidates=[
                MatchPromptCandidate(
                    alias="candidate-0", interests=["music"], cg_name="A"
                ),
                MatchPromptCandidate(
                    alias="candidate-1", interests=["art"], cg_name="B"
                ),
            ]
        )
    )

    assert aliases == ("candidate-1", "candidate-0")
    payload = json.dumps(client.requests[0]["json"])
    assert "candidate-0" in payload and "candidate-1" in payload
    assert "profile-id-sentinel-443" not in payload


async def test_gateway_serializes_only_safe_persona_prompt_content() -> None:
    """Persona transport must preserve authored words while omitting identifier sentinels."""

    client = FakeHttpxClient(
        [FakeResponse(200, {"choices": [{"message": {"content": "calm"}}]})]
    )

    summary = await _gateway(client).summarize_persona(
        PersonaSummaryRequest(
            user_name="A",
            persona="telegram and dob are ordinary words",
            messages=["telegram and dob are ordinary words"],
        )
    )

    serialized = json.dumps(client.requests[0]["json"])
    assert summary == "calm"
    assert all(sentinel not in serialized for sentinel in _IDENTIFIER_SENTINELS)
    assert "telegram" in serialized and "dob" in serialized


async def test_gateway_serializes_only_safe_match_prompt_content() -> None:
    """Match transport must preserve authored words while omitting identifier sentinels."""

    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": '{"key":"system.done"}'}}]},
            )
        ]
    )

    ranked = await _gateway(client).rank_aliases(
        MatchRankingRequest(
            candidates=[
                MatchPromptCandidate(
                    alias="candidate-0",
                    interests=["telegram and dob are ordinary words"],
                    cg_name="telegram dob",
                )
            ]
        )
    )

    serialized = json.dumps(client.requests[0]["json"])
    assert ranked == ("candidate-0",)
    assert all(sentinel not in serialized for sentinel in _IDENTIFIER_SENTINELS)
    assert "telegram" in serialized and "dob" in serialized


@pytest.mark.parametrize(
    "outcome",
    [
        FakeResponse(503, {"untrusted_response": "response-sentinel-503"}),
        OSError("transport-sentinel"),
    ],
)
async def test_gateway_closes_without_attaching_raw_transport_data_after_retry_exhaustion(
    outcome: FakeResponse | Exception,
) -> None:
    """A transient provider failure must retry exactly the bounded count without leaks."""

    client = FakeHttpxClient([outcome] * ROUTING_MAX_ATTEMPTS)
    gateway = _gateway(client)

    with pytest.raises(GatewayTransportError) as raised:
        await gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    assert len(client.requests) == ROUTING_MAX_ATTEMPTS
    assert "response-sentinel-503" not in str(raised.value)
    assert "transport-sentinel" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert vars(raised.value) == {}
    assert not _traceback_gateway_locals_hold_sentinel(
        raised.value, b"response-sentinel-503"
    )
    assert not _traceback_gateway_locals_hold_sentinel(
        raised.value, b"transport-sentinel"
    )


async def test_gateway_clears_outbound_payload_after_transport_exhaustion() -> None:
    """A retaining transport cannot keep a prompt once the gateway has failed."""

    @dataclass
    class RetainingTransportClient:
        headers: list[Mapping[str, str]] = field(default_factory=list)
        payloads: list[Mapping[str, object]] = field(default_factory=list)

        async def post(self, url: str, **kwargs: Any) -> object:
            self.headers.append(kwargs["headers"])
            self.payloads.append(kwargs["json"])
            return object()

    client = RetainingTransportClient()
    gateway = OpenRouterGateway(
        api_key="test-only",
        client=client,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError):
        await gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["private prompt"])
        )

    assert len(client.payloads) == ROUTING_MAX_ATTEMPTS
    assert [dict(headers) for headers in client.headers] == [{}] * ROUTING_MAX_ATTEMPTS
    assert [dict(payload) for payload in client.payloads] == [{}] * ROUTING_MAX_ATTEMPTS
