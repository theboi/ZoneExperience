# F01 Task 3 Implementation Report

## Scope

Implemented only F01 Task 3: recursive `DiscussionFlow` parsing and the
immutable publication-validation boundary. The implementation adds no action
executor, runtime dispatcher, persistence model, migration, service seed, or
Telegram behavior.

## Gate and shared-main evidence

- Read `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/PLANNING_GATE.md`
  before implementation. Its gate state is `passed` and it authorizes the
  complete planning set at `a8249b92d74e6a9a55eefa85dd477a9967c92575`.
- Read the F01 global constraints and Task 3 plan, F01 design, canonical Zone
  X example, and `.superpowers/sdd/2026-09-12-f01-foundation-flow-persistence/task-3-brief.md`.
- Acquired `/tmp/friendly-bot-main-mutation-u504.lock` atomically before the
  initial pull and retained it through this report.
- Pulled before edits. The starting checkout was
  `98a5c264ace85a66e791bd1fc9a0a3e28a11036b`, equal to `origin/main`.

## Test-first evidence

### Initial red

Command:

```sh
cd friendly-bot && uv run pytest tests/unit/domain/test_publication.py -q
```

Result: exit 2 during collection, with the expected missing production
boundary:

```text
ModuleNotFoundError: No module named 'friendly_bot.domain.flows'
```

The fixtures and tests existed before either Task 3 production module.

### Regression red

During the invariant review, a test supplied a raw unknown action to a draft
after Pydantic construction had been bypassed. The focused suite failed as
expected with `AttributeError: 'dict' object has no attribute
'declared_event_keys'` in the event-cycle pre-scan. The minimal repair makes
that scan ignore malformed action objects so the registered action parser
reports the required `FlowPublicationError`. The focused suite then passed
without warnings.

## Delivered contract

- `DiscussionFlow` is the sole recursive Pydantic flow type, with closed Task
  2 trigger/action unions, stable keys, `NextFlowMode`, default empty action
  and child lists, and checkpoint return actions.
- `validate_for_publication(root, context_schema, root_kind)` produces a
  `PublishedFlowDefinition` containing a detached canonical JSON document,
  deterministic SHA-256 content hash, flow-key-to-document-path index, and
  structured no-op-leaf warnings.
- Publication validates recursive unique stable flow keys; registered
  discriminators; triggerless roots; system/service checkpoint roots;
  timestamp roots with any mode; checkpoint child requirements; and
  checkpoint-only `return_actions`.
- It revalidates mutated action/trigger/button data at publication, requires
  each normal declared event outcome to have exactly one immediate
  `ActionEventDiscussionFlowTrigger` child, allows zero or one direct
  `error` child, rejects duplicate direct handlers, and requires event-emitting
  actions to be terminal in either action list.
- Template references are checked recursively in declarative action data,
  accept Zone X's optional-path syntax, require declared dotted context names,
  and reject malformed or non-string bypassed values without serializer
  warnings.
- Event-only direct-child cycles are rejected before canonical serialization;
  canonical JSON sorts keys, uses compact UTF-8 JSON, and rejects non-finite
  JSON values.

## Verification

The following post-merge command sequence exited 0:

```sh
cd friendly-bot && uv run pytest tests/unit/domain/test_publication.py -q
uv run pytest tests/unit/domain/test_triggers.py tests/unit/domain/test_actions.py tests/unit/config/test_settings.py tests/unit/test_package_installation.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src/friendly_bot
uv lock --check
```

Results:

- Publication suite: `17 passed in 0.33s`.
- Relevant Task 1–2 suites: `49 passed in 0.64s`.
- Ruff: all checks passed; 15 files already formatted.
- Strict mypy: no issues in 9 source files.
- Lock check: resolved/validated 26 packages.

## Commit and remote proof

- Staged exactly the four Task 3 paths and ran `git diff --cached --check`:
  - `friendly-bot/src/friendly_bot/domain/flows.py`
  - `friendly-bot/src/friendly_bot/domain/publication.py`
  - `friendly-bot/tests/unit/domain/fixtures.py`
  - `friendly-bot/tests/unit/domain/test_publication.py`
- Commit: `9eefcd5084612fd7ec34c5991ecb22aa7a5cd449`
  (`feat: validate immutable discussion flow publications`).
- Fetched and merged `origin/main` using `git merge --no-edit origin/main`;
  it was already up to date. No rebase was used.
- Pushed `main` from `98a5c26` to `9eefcd5`, fetched again, and ran
  `git merge-base --is-ancestor HEAD origin/main` successfully. Both `HEAD`
  and `origin/main` resolved to
  `9eefcd5084612fd7ec34c5991ecb22aa7a5cd449`.

## Notes

- The local Git credential helper emitted `failed to store: -25308` during
  fetch/push, but every Git command exited 0 and the final remote equality and
  ancestry proof succeeded.
- GitHub reported one pre-existing moderate Dependabot vulnerability on the
  repository default branch. This task did not alter dependencies or the lock
  graph.

## Review round 1: closed-schema and immutability hardening

The first review did not approve the publication boundary. The repair remains
strictly inside Task 3 source, tests, and this report; it adds no executor,
persistence, migration, or transport behavior.

### Review red evidence

The following focused command initially reported seven expected failures:

```sh
cd friendly-bot && uv run pytest tests/unit/domain/test_publication.py -q
```

- A `DiscussionFlow` subclass with a declared extra field was accepted and its
  field appeared in the publication document.
- Mutating `PublishedFlowDefinition.document` or `.flow_key_index` changed the
  stored values while leaving `content_hash` unchanged.
- Post-construction `actions=None`, `return_actions=iter(())`, and
  `next_flows=None` leaked `TypeError` or a Pydantic serializer warning before
  a publication error was raised.
- A direct `action_event` child for an event no action can emit was accepted.
- `{{ user.name | fallback }}` was accepted despite only `| optional` being
  supported by the published template grammar.

### Repair

- Publication now serializes a draft with Pydantic warnings promoted to errors,
  revalidates that data through the exact base `DiscussionFlow` schema, then
  traverses only the normalized base tree. Fieldless subclasses normalize;
  subclass/extra data that cannot fit the base closed schema raises
  `FlowPublicationError`. Malformed containers likewise become
  `FlowPublicationError` without a warning escape.
- `PublishedFlowDefinition` keeps private canonical JSON bytes and a private
  read-only index. Its public `document` and `flow_key_index` properties return
  defensive copies, and the content hash is derived from those canonical bytes
  in the constructor.
- Direct action-event child keys must now be exactly the union of declared
  non-error outcomes plus an optional single `error` child. Unsolicited direct
  event children fail publication.
- The template parser recognizes only `{{ dotted.path }}` and
  `{{ dotted.path | optional }}`. Every other filter is rejected as malformed.
- A physical in-memory event cycle is rejected by canonical base-model
  revalidation before graph traversal, preserving the public
  `FlowPublicationError` boundary without attempting to serialize a cycle.

### Review green evidence

After the minimal repair, fresh checks exited 0:

```sh
cd friendly-bot && uv run pytest tests/unit/domain/test_publication.py -q
uv run pytest tests/unit/domain/test_triggers.py tests/unit/domain/test_actions.py tests/unit/config/test_settings.py tests/unit/test_package_installation.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src/friendly_bot
uv lock --check
```

Results: publication suite `24 passed`; Task 1–2 regression suite `49 passed`;
full current suite `73 passed`; Ruff, formatting, strict mypy, and lock
verification each exited 0.
