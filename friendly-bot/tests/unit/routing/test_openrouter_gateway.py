"""Behavioral coverage for the private OpenRouter boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from friendly_bot.hyperparameters import (
    OPENROUTER_HTTP_MAX_ATTEMPTS,
    OPENROUTER_MAX_RESPONSE_BYTES,
)
from friendly_bot.routing.contracts import (
    KeySelectionRequest,
    MatchPromptCandidate,
    MatchRankingRequest,
    MultiIntentMatches,
    MultiIntentRequest,
    PersonaSummaryRequest,
    PlannedFlowMatch,
    PlannedReply,
    ReplyTemplateSlot,
    RoutingPromptCandidate,
)
from friendly_bot.routing.openrouter_gateway import (
    GatewayError,
    GatewayPrivacyConfigurationError,
    GatewayProtocolError,
    GatewayTransportError,
    OpenRouterGateway,
    OpenRouterSettings,
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


async def test_debug_gateway_logs_openrouter_input_and_output(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Debug mode exposes only the model prompt and its decoded JSON output."""

    raw_body = json.dumps(
        {"choices": [{"message": {"content": '{"key":"flow.a"}'}}]}
    ).encode()

    def successful_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        del args, kwargs
        return RawStdlibResponse(raw_body)

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", successful_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        debug=True,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with caplog.at_level(
        logging.DEBUG, logger="friendly_bot.routing.openrouter_gateway"
    ):
        assert (
            await gateway.select_key(
                KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
            )
            == "flow.a"
        )

    assert "LLM prompt (attempt 1):" in caplog.text
    assert "SYSTEM:" in caplog.text
    assert "USER:" in caplog.text
    assert "hello" in caplog.text
    assert '"allowed_keys"' in caplog.text
    assert 'LLM output:\n{\n  "key": "flow.a"\n}' in caplog.text
    assert "choices" not in caplog.text


def _multi_intent_request() -> MultiIntentRequest:
    return MultiIntentRequest(
        messages=("where is the zone and what happens there?",),
        candidates=(
            RoutingPromptCandidate(
                flow_id="system.directions",
                gists=("asks for directions",),
                possible_qns=("where is the zone?", "how do i get there?"),
                context_label="current",
                multi_intent_mode="answer",
                reply_slots=(
                    ReplyTemplateSlot(
                        slot_id="r0",
                        template="The Zone is at {{ service.name }}. Map: https://example.com/map",
                        template_tokens=("service.name",),
                        urls=("https://example.com/map",),
                    ),
                ),
            ),
            RoutingPromptCandidate(
                flow_id="system.expect",
                gists=("asks what to expect",),
                context_label="current",
                multi_intent_mode="answer",
            ),
        ),
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


async def test_route_and_plan_returns_all_valid_matches_in_one_request() -> None:
    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "kind": "matches",
                                        "matches": [
                                            {
                                                "flow_id": "system.directions",
                                                "replies": [
                                                    {
                                                        "slot_id": "r0",
                                                        "text": "The Zone is at {{ service.name }}. Map: https://example.com/map",
                                                    }
                                                ],
                                            },
                                            {
                                                "flow_id": "system.expect",
                                                "replies": [],
                                            },
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                },
            )
        ]
    )

    result = await _gateway(client).route_and_plan(_multi_intent_request())

    assert result == MultiIntentMatches(
        kind="matches",
        matches=(
            PlannedFlowMatch(
                flow_id="system.directions",
                replies=(
                    PlannedReply(
                        slot_id="r0",
                        text="The Zone is at {{ service.name }}. Map: https://example.com/map",
                    ),
                ),
            ),
            PlannedFlowMatch(flow_id="system.expect"),
        ),
    )
    assert len(client.requests) == 1
    payload = client.requests[0]["json"]
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["name"] == "friendly_bot_multi_intent"
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["matches"]["items"]["properties"]["flow_id"] == {
        "type": "string",
        "enum": ["system.directions", "system.expect"],
    }
    assert payload["provider"]["require_parameters"] is True
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["max_tokens"] == 1024


async def test_route_and_plan_falls_back_only_an_invalid_paraphrase_slot() -> None:
    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "kind": "matches",
                                        "matches": [
                                            {
                                                "flow_id": "system.directions",
                                                "replies": [
                                                    {
                                                        "slot_id": "r0",
                                                        "text": (
                                                            "the zone is at {{ service.name }} "
                                                            + chr(0x2014)
                                                            + " check the map"
                                                        ),
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                },
            )
        ]
    )

    result = await _gateway(client).route_and_plan(
        MultiIntentRequest(
            candidates=(_multi_intent_request().candidates[0],),
        )
    )

    assert isinstance(result, MultiIntentMatches)
    assert result.matches[0].replies[0].text == (
        "The Zone is at {{ service.name }}. Map: https://example.com/map"
    )


async def test_multi_intent_prompt_keeps_the_instruction_static() -> None:
    first_client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"kind":"terminal","terminal":"no_match"}'
                            }
                        }
                    ]
                },
            )
        ]
    )
    second_client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"kind":"terminal","terminal":"no_match"}'
                            }
                        }
                    ]
                },
            )
        ]
    )
    first_request = _multi_intent_request()
    second_request = MultiIntentRequest(
        messages=("is there a toilet?",),
        candidates=(
            RoutingPromptCandidate(
                flow_id="service.toilet",
                gists=("asks for a toilet",),
                context_label="service",
                multi_intent_mode="answer",
            ),
        ),
    )

    await _gateway(first_client).route_and_plan(first_request)
    await _gateway(second_client).route_and_plan(second_request)

    first_payload = first_client.requests[0]["json"]
    second_payload = second_client.requests[0]["json"]
    assert first_payload["messages"][0] == second_payload["messages"][0]
    assert first_payload["messages"][1] != second_payload["messages"][1]
    assert "system.directions" not in first_payload["messages"][0]["content"]
    assert (
        "every character you generate in lowercase"
        in first_payload["messages"][0]["content"]
    )
    assert (
        "including the first word, names, and titles"
        in first_payload["messages"][0]["content"]
    )
    assert (
        "ALL_CAPS placeholders (including underscores) verbatim"
        in first_payload["messages"][0]["content"]
    )
    assert "gist or possible_qns" in first_payload["messages"][0]["content"]
    assert (
        "shared keyword, topic, place, name, or available candidate never "
        "establishes intent" in first_payload["messages"][0]["content"]
    )
    assert (
        "'zone?' and 'the zone?' are ambiguous"
        in first_payload["messages"][0]["content"]
    )
    assert (
        "valid answer to the current context" in first_payload["messages"][0]["content"]
    )
    request_body = json.loads(first_payload["messages"][1]["content"])
    assert request_body["candidates"][0]["possible_qns"] == [
        "where is the zone?",
        "how do i get there?",
    ]


@pytest.mark.parametrize(
    "content",
    [
        {"kind": "matches", "matches": []},
        {
            "kind": "matches",
            "matches": [
                {"flow_id": "system.directions", "replies": []},
            ],
        },
        {
            "kind": "matches",
            "matches": [
                {"flow_id": "unknown.flow", "replies": []},
            ],
        },
        {"flows": [{"flow_id": "system.directions", "reply": "ignored"}]},
        {"kind": "terminal", "terminal": "no_match", "extra": True},
    ],
)
async def test_route_and_plan_rejects_malformed_or_incomplete_results(
    content: dict[str, object],
) -> None:
    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": json.dumps(content)}}]},
            )
        ]
    )

    with pytest.raises(GatewayProtocolError):
        await _gateway(client).route_and_plan(_multi_intent_request())


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

    monkeypatch.setitem(OpenRouterSettings.model_config, "env_file", None)
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


@pytest.mark.parametrize("attestation", [0, 0.0, True, "False", "true"])
def test_openrouter_settings_rejects_false_lookalikes(attestation: object) -> None:
    """Only the exact dotenv text `false` may represent disabled logging."""

    with pytest.raises(ValidationError):
        OpenRouterSettings.model_validate(
            {"friendly_bot_openrouter_input_output_logging_attestation": attestation}
        )


def test_openrouter_settings_reads_key_and_attestation_from_explicit_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv(
        "FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION", raising=False
    )
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "OPENROUTER_API_KEY=dotenv-openrouter-key\n"
        "FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION="
        "disabled-globally-or-friendly-bot-key-excluded\n"
    )

    settings = OpenRouterSettings(_env_file=dotenv_file)

    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "dotenv-openrouter-key"
    assert (
        settings.friendly_bot_openrouter_input_output_logging_attestation
        == _OBSERVABILITY_ATTESTATION
    )


def test_openrouter_settings_reads_an_explicit_test_model_without_zdr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator can temporarily test a non-ZDR model without a source edit."""

    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("FRIENDLY_BOT_OPENROUTER_ENFORCE_ZDR", raising=False)
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "OPENROUTER_MODEL=qwen/qwen3.7-flash\n"
        "FRIENDLY_BOT_OPENROUTER_ENFORCE_ZDR=false\n"
    )

    settings = OpenRouterSettings(_env_file=dotenv_file)

    assert settings.openrouter_model == "qwen/qwen3.7-flash"
    assert settings.friendly_bot_openrouter_enforce_zdr is False


def test_gateway_environment_does_not_retain_invalid_attestation_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid configuration must cross the public boundary as a static error."""

    invalid_attestation = "invalid-attestation-sentinel-r03"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv(
        "FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION",
        invalid_attestation,
    )

    with pytest.raises(GatewayPrivacyConfigurationError) as raised:
        OpenRouterSettings.from_environment()

    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert invalid_attestation not in str(error)
    assert all(invalid_attestation not in str(argument) for argument in error.args)
    assert vars(error) == {}


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
    assert payload["model"] == "google/gemini-2.5-flash"
    assert payload["provider"] == {"zdr": True, "data_collection": "deny"}
    assert set(payload) == {"model", "provider", "messages"}
    assert all(sentinel not in serialized for sentinel in _IDENTIFIER_SENTINELS)
    assert "telegram" in serialized
    assert "dob" in serialized


async def test_gateway_uses_an_explicit_test_model_without_zdr_enforcement() -> None:
    """The testing override must reach the provider payload, not just settings."""

    client = FakeHttpxClient(
        [
            FakeResponse(
                200,
                {"choices": [{"message": {"content": '{"key":"flow.a"}'}}]},
            )
        ]
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        client=client,
        model="qwen/qwen3.7-flash",
        enforce_zdr=False,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    assert (
        await gateway.select_key(KeySelectionRequest(allowed_keys={"flow.a"}))
        == "flow.a"
    )
    assert client.requests[0]["json"]["model"] == "qwen/qwen3.7-flash"
    assert client.requests[0]["json"]["provider"] == {"data_collection": "deny"}


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
    expected_attempts = OPENROUTER_HTTP_MAX_ATTEMPTS
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


async def test_gateway_normalizes_stdlib_timeout_to_static_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real stdlib socket timeout must not escape the static error boundary."""

    timeout_detail = "socket-timeout-sentinel-r03"
    attempts = 0

    def timed_out_urlopen(*args: object, **kwargs: object) -> RawStdlibResponse:
        nonlocal attempts
        attempts += 1
        raise TimeoutError(timeout_detail)

    monkeypatch.setattr(
        "friendly_bot.routing.openrouter_gateway.urlopen", timed_out_urlopen
    )
    gateway = OpenRouterGateway(
        api_key="test-only",
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )

    with pytest.raises(GatewayTransportError) as raised:
        await gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    assert attempts == OPENROUTER_HTTP_MAX_ATTEMPTS
    _assert_closed_error_has_no_provider_bytes(raised.value, timeout_detail.encode())


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

    assert attempts == OPENROUTER_HTTP_MAX_ATTEMPTS
    assert [body.read_calls for body in error_bodies] == [
        0
    ] * OPENROUTER_HTTP_MAX_ATTEMPTS
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
    client = FakeHttpxClient(
        [IncompleteJsonResponse(raw_body)] * OPENROUTER_HTTP_MAX_ATTEMPTS
    )

    with pytest.raises(GatewayTransportError) as raised:
        await operation(_gateway(client))

    assert len(client.requests) == OPENROUTER_HTTP_MAX_ATTEMPTS
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

    assert attempts == OPENROUTER_HTTP_MAX_ATTEMPTS
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
                "model": "google/gemini-2.5-flash",
                "provider": {"zdr": True, "data_collection": "deny"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Choose a configured flow only when the user's current "
                            "message clearly satisfies its gist. Use system.no_match "
                            "when none does. A safety flow requires an explicit "
                            "disclosure of immediate danger, abuse, self-harm, or an "
                            "urgent request for a trusted adult; do not infer it from "
                            "an ambiguous request for help. "
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

    client = FakeHttpxClient([outcome] * OPENROUTER_HTTP_MAX_ATTEMPTS)
    gateway = _gateway(client)

    with pytest.raises(GatewayTransportError) as raised:
        await gateway.select_key(
            KeySelectionRequest(allowed_keys={"flow.a"}, messages=["hello"])
        )

    assert len(client.requests) == OPENROUTER_HTTP_MAX_ATTEMPTS
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

    assert len(client.payloads) == OPENROUTER_HTTP_MAX_ATTEMPTS
    assert [dict(headers) for headers in client.headers] == [
        {}
    ] * OPENROUTER_HTTP_MAX_ATTEMPTS
    assert [dict(payload) for payload in client.payloads] == [
        {}
    ] * OPENROUTER_HTTP_MAX_ATTEMPTS
