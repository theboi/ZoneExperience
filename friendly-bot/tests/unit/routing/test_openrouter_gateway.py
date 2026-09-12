"""Behavioral coverage for the private OpenRouter boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from friendly_bot.routing.contracts import (
    KeySelectionRequest,
    MatchPromptCandidate,
    MatchRankingRequest,
    PersonaSummaryRequest,
)
from friendly_bot.routing.openrouter_gateway import (
    GatewayProtocolError,
    OpenRouterGateway,
)


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.payload


@dataclass
class FakeHttpxClient:
    responses: list[FakeResponse]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return self.responses.pop(0)


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
    gateway = OpenRouterGateway(api_key="test-only", client=client)
    request = KeySelectionRequest(
        allowed_keys={"flow.a"},
        user_name="A",
        messages=["I can say telegram and dob in ordinary text"],
    )

    assert await gateway.select_key(request) == "flow.a"

    payload = client.requests[0]["json"]
    serialized = json.dumps(payload)
    assert payload["model"] == "qwen/qwen3.7-flash"
    assert payload["provider"] == {"zdr": True, "data_collection": "deny"}
    assert payload["logprobs"] is False
    assert "telegram-id-sentinel-729" not in serialized
    assert "telegram-chat-sentinel-418" not in serialized
    assert "dob-sentinel-2001-02-03" not in serialized
    assert "telegram" in serialized
    assert "dob" in serialized


@pytest.mark.parametrize(
    "content",
    [
        '{"key":"flow.a","extra":"no"}',
        '{"key":"flow.unknown"}',
        '["flow.a"]',
        'not json',
    ],
)
async def test_gateway_fails_closed_for_non_single_whitelisted_key(content: str) -> None:
    """Relaxing exact-key parsing would turn model prose into routing authority."""

    client = FakeHttpxClient(
        [FakeResponse(200, {"choices": [{"message": {"content": content}}]})]
    )
    gateway = OpenRouterGateway(api_key="test-only", client=client)

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
    gateway = OpenRouterGateway(api_key="test-only", client=client)

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
            FakeResponse(200, {"choices": [{"message": {"content": '{"key":"system.done"}'}}]}),
        ]
    )
    gateway = OpenRouterGateway(api_key="test-only", client=client)

    aliases = await gateway.rank_aliases(
        MatchRankingRequest(
            candidates=[
                MatchPromptCandidate(alias="candidate-0", interests=["music"], cg_name="A"),
                MatchPromptCandidate(alias="candidate-1", interests=["art"], cg_name="B"),
            ]
        )
    )

    assert aliases == ("candidate-1", "candidate-0")
    payload = json.dumps(client.requests[0]["json"])
    assert "candidate-0" in payload and "candidate-1" in payload
    assert "profile-id-sentinel-443" not in payload
