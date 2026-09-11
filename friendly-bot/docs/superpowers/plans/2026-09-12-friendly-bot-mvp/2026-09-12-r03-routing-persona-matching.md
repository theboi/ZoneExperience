# R03 Constrained Routing, Persona, and Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement private key-only OpenRouter routing, cursor-based persona generation, and durable normal/safety human matching.

**Architecture:** R03 builds pure prompt and policy DTOs around F01 domain/persistence contracts. The gateway accepts only a single whitelisted key, routing repeatedly selects distinct flow keys, persona cursors advance only after a successful summary, and F01 guarded repositories make match reservation durable and concurrent-safe.

**Tech Stack:** Python 3.12, httpx, Pydantic v2, pytest/pytest-asyncio, SQLAlchemy/PostgreSQL through F01 `UnitOfWork`.

**Spec:** `friendly-bot/docs/superpowers/specs/2026-09-12-friendly-bot-mvp/2026-09-12-r03-routing-persona-matching-design.md`

## Global Constraints

- Start only after coordinator-passed `PG` and G1 F01 evidence are both reachable on `origin/main`.
- Use F01's `UnitOfWork` and repository DTOs; add no schema, migration, ORM session, adapter, compatibility shim, or fallback store.
- Use `qwen/qwen3.7-flash` only from `hyperparameters.py`; API keys remain environment-only.
- Every OpenRouter request sets ZDR, denies collection, disables prompt logging, and contains no structured Telegram ID or DOB.
- The model returns configured/reserved keys only; the harness alone renders fixed user-facing copy.

## File map

| Path | Responsibility |
| --- | --- |
| `src/friendly_bot/hyperparameters.py` | Non-secret model, retry, timeout, and persona thresholds |
| `src/friendly_bot/routing/contracts.py` | Prompt-safe DTOs and reserved keys |
| `src/friendly_bot/routing/openrouter_gateway.py` | Compliant HTTP request and strict single-key parsing |
| `src/friendly_bot/routing/router.py` | Candidate assembly and repeated selection loop |
| `src/friendly_bot/persona/service.py` | Durable cursor eligibility, summary, and advance |
| `src/friendly_bot/matching/service.py` | Exact-role eligibility, alias ranking, reservation, rematch |
| `tests/unit/routing/`, `tests/unit/persona/`, `tests/unit/matching/` | Policy and redaction tests |
| `tests/integration/routing/`, `tests/integration/matching/` | F01-UoW and concurrent-reservation tests |

### Task 1: Prompt-safe configuration and OpenRouter gateway

**Files:** Create `friendly-bot/src/friendly_bot/hyperparameters.py`, `routing/__init__.py`, `routing/contracts.py`, `routing/openrouter_gateway.py`; create `friendly-bot/tests/unit/routing/test_openrouter_gateway.py`.

**Interfaces:** Produces `OpenRouterGateway.select_key(request: KeySelectionRequest) -> str`, `KeySelectionRequest`, and `OPENROUTER_MODEL`. Consumes `OPENROUTER_API_KEY`.

- [ ] **Step 1: Write failing gateway/privacy tests.**

```python
@pytest.mark.parametrize("forbidden", [
    {"telegram_user_id": "telegram-id-sentinel-729"},
    {"telegram_chat_id": "telegram-chat-sentinel-418"},
    {"dob": "dob-sentinel-2001-02-03"},
])
def test_prompt_dto_forbids_structured_identity_fields(forbidden: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        KeySelectionRequest(allowed_keys={"flow.a"}, user_name="A", messages=["telegram and dob are ordinary words"], **forbidden)

async def test_request_requires_policy_and_excludes_identifier_sentinels(httpx_mock: HTTPXMock) -> None:
    gateway = OpenRouterGateway(api_key="secret", client=AsyncClient())
    httpx_mock.add_response(json={"choices": [{"message": {"content": '{"key":"flow.a"}'}}]})
    request = KeySelectionRequest(allowed_keys={"flow.a"}, user_name="A", messages=["I can say telegram and dob in ordinary text"])
    await gateway.select_key(request)
    body = json.dumps(httpx_mock.get_requests()[0].json())
    assert '"zdr": true' in body and '"data_collection": "deny"' in body
    assert "telegram-id-sentinel-729" not in body and "telegram-chat-sentinel-418" not in body
    assert "dob-sentinel-2001-02-03" not in body and "telegram" in body and "dob" in body
```

- [ ] **Step 2: Run the failing test.** Run: `cd friendly-bot && uv run pytest tests/unit/routing/test_openrouter_gateway.py -q`. Expected: FAIL because the R03 modules do not exist.
- [ ] **Step 3: Implement the strict gateway.** Put `model_config = ConfigDict(extra="forbid")` on every prompt DTO. Define constants `OPENROUTER_MODEL = "qwen/qwen3.7-flash"`, `ROUTING_MAX_ATTEMPTS = 3`, `PERSONA_IDLE_AFTER = timedelta(hours=48)`, and `PERSONA_MAX_UNSUMMARIZED_TOKENS`. Post only prompt-safe DTO serialization with `provider={"zdr": True, "data_collection": "deny"}` and `logprobs=False`; parse exactly `{"key": str}` and reject a key outside `allowed_keys`.
- [ ] **Step 4: Run focused checks.** Run: `cd friendly-bot && uv run pytest tests/unit/routing/test_openrouter_gateway.py -q && uv run ruff check src/friendly_bot/routing tests/unit/routing && uv run mypy src/friendly_bot/routing`. Expected: exit 0.
- [ ] **Step 5: Commit.** Run: `git add friendly-bot/src/friendly_bot/hyperparameters.py friendly-bot/src/friendly_bot/routing friendly-bot/tests/unit/routing/test_openrouter_gateway.py && git commit -m "feat: add private OpenRouter key gateway"`.

### Task 2: Candidate assembly and repeated constrained routing

**Files:** Modify `routing/contracts.py`; create `routing/router.py`, `tests/unit/routing/test_router.py`, `tests/integration/routing/test_router_uow.py`.

**Interfaces:** Consumes F01 `OpenSelectionState`, `DiscussionFlow`, `MessageDiscussionFlowTrigger`, and `OpenSelectionRepository.list_for_user`. Produces `ConstrainedRouter.route_update(user_id: UUID, incoming: IncomingText, now: datetime) -> RoutingResult` and `CandidateAssembler.assemble(...) -> list[RoutingCandidate]`.

- [ ] **Step 1: Write failing routing tests.**

```python
async def test_router_selects_two_distinct_keys_then_done() -> None:
    gateway = StubGateway(["flow.current", "flow.global", "system.done"])
    result = await router.route_update(USER, IncomingText(body="help", replied_to_body="old question"), NOW)
    assert [selection.key for selection in result.selections] == ["flow.current", "flow.global"]

async def test_ambiguous_and_no_match_are_terminals() -> None:
    assert (await router_with("system.clarify_ambiguous_context").route_update(USER, TEXT, NOW)).terminal == RoutingTerminal.CLARIFY
    assert (await router_with("system.no_match").route_update(USER, TEXT, NOW)).terminal == RoutingTerminal.NO_MATCH
```

- [ ] **Step 2: Run the failing test.** Run: `cd friendly-bot && uv run pytest tests/unit/routing/test_router.py tests/integration/routing/test_router_uow.py -q`. Expected: FAIL because candidate assembler and router are absent.
- [ ] **Step 3: Implement candidate and terminal rules.** Load current, reusable-past, system-global, service-global, and valid service selections together. Traverse only message-trigger descendants, include native reply body as prompt context, remove a selected key before the next call, reject duplicates, cap at `ROUTING_MAX_ATTEMPTS`, and map only `system.done`, `system.no_match`, and `system.clarify_ambiguous_context` to terminals. Never persist reply-message IDs.
- [ ] **Step 4: Run focused checks.** Run: `cd friendly-bot && uv run pytest tests/unit/routing/test_router.py tests/integration/routing/test_router_uow.py -q && uv run ruff check src/friendly_bot/routing tests/unit/routing tests/integration/routing && uv run mypy src/friendly_bot/routing`. Expected: exit 0.
- [ ] **Step 5: Commit.** Run: `git add friendly-bot/src/friendly_bot/routing friendly-bot/tests/unit/routing/test_router.py friendly-bot/tests/integration/routing/test_router_uow.py && git commit -m "feat: route only configured flow keys"`.

### Task 3: Cursor-safe persona maintenance

**Files:** Create `friendly-bot/src/friendly_bot/persona/__init__.py`, `persona/service.py`, `tests/unit/persona/test_service.py`, `tests/integration/routing/test_persona_cursor.py`.

**Interfaces:** Consumes F01 `UnitOfWork.personas.get_or_create/advance` and `UnitOfWork.conversations.list_after`. Produces `PersonaMaintenanceService.maintain(user_id: UUID, now: datetime) -> PersonaMaintenanceResult`.

- [ ] **Step 1: Write failing cursor tests.**

```python
async def test_success_advances_cursor_without_deleting_messages() -> None:
    result = await service.maintain(USER, NOW)
    assert result.generated is True
    assert repo.cursor.last_message_id == newest.id
    assert repo.messages == original_messages

async def test_gateway_failure_preserves_old_cursor() -> None:
    await service_with_failure.maintain(USER, NOW)
    assert repo.cursor.last_message_id == old_cursor
```

- [ ] **Step 2: Run the failing test.** Run: `cd friendly-bot && uv run pytest tests/unit/persona/test_service.py tests/integration/routing/test_persona_cursor.py -q`. Expected: FAIL because persona service is absent.
- [ ] **Step 3: Implement eligibility and atomic advance.** Fetch messages after the durable cursor, count tokens with the selected-model tokenizer, regenerate after 48-hour inactivity or threshold only, and call `advance` after the gateway returns a nonempty summary. Keep source rows and old cursor on all failed calls.
- [ ] **Step 4: Run focused checks.** Run: `cd friendly-bot && uv run pytest tests/unit/persona/test_service.py tests/integration/routing/test_persona_cursor.py -q && uv run ruff check src/friendly_bot/persona tests/unit/persona && uv run mypy src/friendly_bot/persona`. Expected: exit 0.
- [ ] **Step 5: Commit.** Run: `git add friendly-bot/src/friendly_bot/persona friendly-bot/tests/unit/persona/test_service.py friendly-bot/tests/integration/routing/test_persona_cursor.py && git commit -m "feat: maintain persona from durable cursor"`.

### Task 4: Normal matching and rematch policy

**Files:** Create `friendly-bot/src/friendly_bot/matching/__init__.py`, `matching/service.py`, `tests/unit/matching/test_normal_matching.py`, `tests/integration/matching/test_reservations.py`.

**Interfaces:** Consumes F01 `UnitOfWork.matches.list_eligible_normal`, `.reserve_ranked`, `.release_and_exclude`, and `lock_user`. Produces `MatchingService.reserve_normal(request_id: UUID, service_id: UUID, now: datetime) -> MatchAssignmentRecord | None` and `.rematch_normal(...)`.

- [ ] **Step 1: Write failing normal-match tests.**

```python
async def test_normal_pool_is_exact_server_attendees_with_capacity() -> None:
    assignment = await service.reserve_normal(REQUEST, SERVICE, NOW)
    assert assignment.responder_profile_id == attending_server.id
    assert leader.id not in gateway.rank_prompt_profile_ids

async def test_rematch_releases_and_excludes_before_new_reservation() -> None:
    await service.rematch_normal(REQUEST, old_profile.id, SERVICE, NOW)
    assert old_profile.id in repository.exclusions_for(REQUEST)
    assert repository.reservation_for(old_profile.id).released_at == NOW
```

- [ ] **Step 2: Run the failing test.** Run: `cd friendly-bot && uv run pytest tests/unit/matching/test_normal_matching.py tests/integration/matching/test_reservations.py -q`. Expected: FAIL because matching service is absent.
- [ ] **Step 3: Implement local aliases and guarded reserve.** Filter exact `server`, current-service attendance, free capacity, and per-request exclusions before gateway ranking. Send aliases, interests, and `cg_name` only; map aliases locally to UUIDs. In one F01 UoW lock the requester, reserve ranked candidates, then on rematch release/exclude before invoking rank/reserve again. Never select absent users, leaders, or staff.
- [ ] **Step 4: Run focused checks.** Run: `cd friendly-bot && uv run pytest tests/unit/matching/test_normal_matching.py tests/integration/matching/test_reservations.py -q && uv run ruff check src/friendly_bot/matching tests/unit/matching tests/integration/matching && uv run mypy src/friendly_bot/matching`. Expected: exit 0.
- [ ] **Step 5: Commit.** Run: `git add friendly-bot/src/friendly_bot/matching friendly-bot/tests/unit/matching/test_normal_matching.py friendly-bot/tests/integration/matching/test_reservations.py && git commit -m "feat: reserve eligible normal matches"`.

### Task 5: Safety matching and concurrency proof

**Files:** Modify `matching/service.py`, `tests/unit/matching/test_safety_matching.py`, `tests/integration/matching/test_reservations.py`.

**Interfaces:** Consumes F01 `MatchRepository.list_eligible_safety` and guarded `.reserve_ranked`. Produces `MatchingService.reserve_safety(request_id: UUID, service_id: UUID | None, now: datetime) -> MatchAssignmentRecord | None`.

- [ ] **Step 1: Write failing safety/concurrency tests.**

```python
async def test_safety_allows_leader_or_staff_attending_or_always_available() -> None:
    assert (await service.reserve_safety(REQUEST, SERVICE, NOW)).responder_profile_id == staff_always_available.id

async def test_one_capacity_slot_has_one_concurrent_winner() -> None:
    results = await asyncio.gather(*[service.reserve_normal(request, SERVICE, NOW) for request in REQUESTS])
    assert sum(result is not None for result in results) == 1
```

- [ ] **Step 2: Run the failing test.** Run: `cd friendly-bot && uv run pytest tests/unit/matching/test_safety_matching.py tests/integration/matching/test_reservations.py -q`. Expected: FAIL because safety policy is absent.
- [ ] **Step 3: Implement separate safety pool.** Permit only `leader`/`staff` candidates satisfying attendance or `always_available`; return `None` when absent and do not query normal candidates. Reuse only F01 guarded reservation and prove the concurrent capacity winner count in PostgreSQL.
- [ ] **Step 4: Run focused checks.** Run: `cd friendly-bot && uv run pytest tests/unit/matching/test_normal_matching.py tests/unit/matching/test_safety_matching.py tests/integration/matching/test_reservations.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot`. Expected: exit 0.
- [ ] **Step 5: Commit.** Run: `git add friendly-bot/src/friendly_bot/matching friendly-bot/tests/unit/matching friendly-bot/tests/integration/matching/test_reservations.py && git commit -m "feat: add safe responder matching"`.

### Task 6: R03 acceptance, review, and execution handoff

**Files:** Modify `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/status/r03.md`; create `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/r03-execution.json`.

**Interfaces:** Consumes all completed Tasks 1–5 and their F01 G1 contract evidence. Produces the execution completion manifest consumed by G2 and I04.

- [ ] **Step 1: Run the complete R03 focused evidence once at the final feature commit.**

Run: `cd friendly-bot && uv run pytest tests/unit/routing tests/unit/persona tests/unit/matching tests/integration/routing tests/integration/matching -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot`

Expected: every command exits 0; record the exact final feature commit from `git rev-parse HEAD` and this command in the execution manifest.

- [ ] **Step 2: Write the execution status and manifest from fresh evidence.**

Set `status/r03.md` to execution-complete only if Step 1 passed and `git merge-base --is-ancestor <final-feature-commit> origin/main` succeeds after push. Create lowercase JSON with `worker`, `phase`, `status`, the exact final feature commit, focused command/output summary, F01 G1 commit inspected, privacy-negative test names, review result, and remote reachability command/result. Do not claim G2 passed; G2 requires T02 evidence and coordinator verification.

- [ ] **Step 3: Request review before publishing the handoff.**

Submit the exact R03 feature diff, focused-suite output, privacy-negative evidence, and execution manifest to the repository review workflow. Resolve every actionable R03-owned finding, rerun the affected focused command, and record the review identifier/result in `r03-execution.json`.

- [ ] **Step 4: Validate and commit the handoff.**

Run: `python3 -m json.tool friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/r03-execution.json && git diff --check && git add friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/status/r03.md friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/r03-execution.json && git commit -m "docs: record R03 execution evidence"`

Expected: valid JSON, no whitespace errors, and only the two handoff paths staged.

- [ ] **Step 5: Reconcile, push, and prove remote evidence.**

Run: `git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/unit/routing tests/unit/persona tests/unit/matching tests/integration/routing tests/integration/matching -q && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: affected suite passes after reconciliation; push succeeds; the handoff commit is reachable from `origin/main`.

## Plan self-review

The six tasks cover provider policy/redaction, strict keys and multi-selection terminals, cursor-only persona advancement, exact normal and safety pools, rematch exclusions, concurrency, review, full R03 acceptance evidence, and a G2-consumable execution handoff. They create no product-policy changes, action registry, migration, seed, or Telegram implementation. Interface names match F01's published UoW/repository ownership; actual execution verifies their equivalent direct methods at G1 before code is started.
