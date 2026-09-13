"""Behavioral coverage for the private OpenRouter boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from friendly_bot.hyperparameters import ROUTING_MAX_ATTEMPTS
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
        return self.payload


@dataclass
class FakeHttpxClient:
    responses: list[FakeResponse | Exception]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"url": url, **kwargs})
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _gateway(client: FakeHttpxClient) -> OpenRouterGateway:
    return OpenRouterGateway(
        api_key="test-only",
        client=client,
        input_output_logging_attestation=_OBSERVABILITY_ATTESTATION,
    )


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
