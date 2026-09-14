# T02 Telegram-Native Output and I04 Completion Design

| Field | Value |
| --- | --- |
| Status | Approved for execution by Ryan The on 2026-09-14 |
| Scope | Bounded T02 outbound/callback/asset repair, minimal F01 match API completion, then I04 composition and Zone X acceptance |
| Runtime operator | `ryanthe` only; historical `bot2` resources are out of scope |

## Goal

Zone X already declares inline buttons, a poster, and typing indicators. T02 currently
accepts only text-shaped durable deliveries, so I04 cannot honestly execute the canonical
configuration. This design supplies the missing typed transport boundary without a new
transport, persistence schema, or callback trust boundary.

## Ownership and durable contracts

T02 owns Telegram-native DTOs, Bot API serialization, outbound-worker parsing, polling
normalization, and asset resolution. F01 retains its provider-neutral `kind` plus JSON
payload outbox records and its claim/start/finalize fencing. I04 composes action output;
it does not call Telegram directly or inspect F01 ORM rows.

The only accepted durable payload shapes are:

| `kind` | Exact JSON shape |
| --- | --- |
| `message` | `{ "text": string, "buttons": [{ "text": string, "callback_data": string }] }` |
| `photo` | `{ "asset_key": string, "caption": string, "buttons": [{ "text": string, "callback_data": string }] }` |

`buttons` may be an empty array. All other keys, types, and operation kinds are rejected
before the network boundary with a static safe error. Existing text-only rows remain
supported by mapping the legacy exact `{ "text": string }` shape to an empty button list.
No migration is needed.

`OutboundTelegramText` and `OutboundTelegramPhoto` form the closed send union. The
client maps them only to `sendMessage` and multipart `sendPhoto`, respectively. A successful
content operation retains the existing positive Telegram message-ID confirmation. `typing`
is a separate typed `sendChatAction` operation: it is intentionally immediate and
non-durable because Telegram clears activity quickly; queuing it behind durable work would
often show activity after the operation it describes has completed. Its result is reduced to
the existing parsed send outcome classes without retaining raw responses.

## Callback contract

Telegram callback data is limited to 1–64 UTF-8 bytes. T02 encodes and decodes a closed
`TelegramCallback` DTO at the transport boundary:

| Form | Meaning |
| --- | --- |
| `<stable-button-id>` | A static configured button |
| `1|<stable-button-id>|s|<base64url-uuid>` | Selected service context |
| `1|<stable-button-id>|m|<base64url-uuid>` | Human-match request context |

The codec rejects an invalid version, delimiter layout, stable button ID, context kind,
UUID encoding, or byte length. It does not add an HMAC: every decoded value is hostile input
and the application must revalidate service lifecycle/eligibility and match-request ownership
inside PostgreSQL before taking action. The raw callback string never crosses from T02 into
I04; dispatch receives a typed button ID plus optional typed context UUID.

F01 replaces `ButtonDefinition.payload: dict[str, JsonValue] | None` with a closed
configuration payload union. The canonical `{ "service_key": "zone_x_2026_10_18" }`
form remains valid; I04 resolves that configured key to the current `ServiceRecord` and emits
the service callback form. Human-match context is injected only for the local retained match
request and is never read from arbitrary configuration JSON.

## Asset contract

`TelegramAssetResolver.resolve_photo(asset_key)` returns an immutable resolved local
photo only when its catalog entry has a repository-relative path, permitted MIME type, and
matching SHA-256. It rejects unknown keys, path traversal, asset-root escape, symlinks
escaping the root, missing files, MIME mismatch, and hash mismatch.

The approved development asset is the user-provided
`assets/zone_x_poster_2026.png`. `assets/catalog.json` binds the stable canonical key
`zone_x_poster_2026` to that path, `image/png`, and its committed SHA-256. The key—not a
filesystem path or file content—is persisted in the durable delivery. This keeps delayed
retries deterministic while avoiding data blobs in PostgreSQL.

## I04 presentation composition

`ActionContext` keeps one local pending presentation. A `send_message` followed by textless
`send_buttons` becomes one text delivery with an inline keyboard; a `send_photo` followed by
textless `send_buttons` becomes one photo delivery with that keyboard; `send_buttons` with
text becomes its own text delivery. Before a non-presentation action, terminal event, direct
child, or flow completion, the runner flushes the pending presentation. A textless button
action without a compatible pending presentation is a deterministic action error—never an
invisible placeholder message.

`send_service_choice_buttons` is itself a visible dynamic presentation, not a textless
keyboard attachment: it carries required fixed `text` plus a stable button ID, while I04
derives only the current service records and callback UUIDs locally. This closes the prior
orphan-keyboard contradiction without embedding service IDs or executable behavior in JSON.

The canonical source preserves its established uppercase YAML enum spellings. I04 normalizes
only `next_flow_mode` and timestamp `audience` enum values to the already-existing lowercase
F01 wire values while loading the semantic JSON copy; all user copy, keys, timestamps, action
parameters, and ordering remain unchanged.

## Minimal F01 matching contract completion

I04 needs public typed persistence operations to create a match request, prove requester
ownership, set meeting preference, read the current assignment, read the assigned responder
contact, and release/exclude an assignment. F01 adds frozen request/contact DTOs and guarded
repository methods that use its existing service/request/capacity locking rules. I04 uses
those operations only; it neither traverses SQLAlchemy models nor creates another session.
R03's candidate eligibility and ranking policy do not change.

The repair also corrects a real operational-login mismatch in the existing query: an
`OperationalProfile` belongs to the role-owner user, but its active Telegram identity is the
attached `OperationalLogin.user_id`. Eligibility must read the exact role from the owner,
but attendance, recipient display name, and chat ID from the active attached user. Candidates
without an active attached Telegram user, positive chat ID, qualifying attendance, or capacity
are ineligible. Tests must use the real split identity rather than fixtures that attach
attendance to the role owner.

The frozen DTOs are `MatchRequestRecord` (requester, optional service, normal/safety kind,
interest, meeting preference, status, timestamps) and `MatchResponderRecord` (assignment,
profile, active recipient user/chat, local display name, CG name, contact URL). The repository
offers guarded get-or-create request, requester-owned interest/confirmation/request lookup,
current responder, release-active responder, and exclusion operations. Reservation marks a
request reserved; confirmation is idempotent only for the same preference; release is one
atomic assignment/reservation/capacity update and returns the prior recipient; service expiry
also resolves released requests.

I04 already runs under T02's user-locked ingress UoW, so `MatchingService` must additionally
offer caller-owned-UoW reserve/safety/rematch methods. It must not open a second session from
inside action execution. Every service-bound mutation follows one order: requester user lock,
service lock/open-boundary fence, request lock, active assignment lock, reservation lock, then
the guarded profile-capacity update. This repair creates no table or migration.

## Runtime decision

All future local acceptance work uses `ryanthe` (UID 501) and a new, checked namespace and
loopback port. No source, documentation, container, volume, lock, or state path under the
historical `bot2` profile is read or mutated.
