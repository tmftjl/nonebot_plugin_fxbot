---
name: fxbot-ai-chat
description: Implementation workflow for FxBot AI memory and group-chat behavior. Use when implementing or reviewing chat memory, history persistence, debounce, sending, quoting, planner, or proactive gate work. Read ../fxbot/SKILL.md and docs/ai-memory-and-chat-design.md first. Do not use for generic NoneBot questions or unrelated subplugins.
---

# FxBot AI Chat Development

Implement the AI chat subsystem described in `docs/ai-memory-and-chat-design.md`. The design document is the source of truth for phases, tables, invariants, and verification; this skill only defines how to work.

## Read First

1. `../fxbot/SKILL.md` for repository-wide standards.
2. `docs/ai-memory-and-chat-design.md`, especially section 22 for P0/P0.5 and section 11.1 for P5.
3. If the change touches `plugins/`, also read `../fxbot-subplugin/SKILL.md`.
4. Use `rg` to read the actual call chain before editing. Do not rely on line numbers or inventories in the design document.

## Scope

Use this skill for:

- `chat/` service, router, session, message adapter, types, providers, tools;
- chat DB models, repository, migrations, bootstrap imports;
- chat configuration, console routes, and chat console UI;
- memory extraction, retrieval, profile, episodes, and lifecycle;
- debounce, splitting, sending, quoting, interruption, planner, and proactive gate;
- `chat/focus/` shared-group planning (MaiBot `src/maisaka/focus/`).

Do not use it for unrelated framework, membership, console, or subplugin work.

## Phase Boundary

Implement one phase item at a time. Do not pull P5 behavior into P0/P0.5.

| Phase | Work |
|---|---|
| P0-1 | Message/event/turn identity, dedupe key, reply target ID |
| P0-2 | `chat_*` models, migrations, UTC, degraded DB startup |
| P0-3 | History persistence, hydration, image-description update |
| P0-4 | Turn states, generated-vs-sent, send failure handling |
| P0-5 | Clear, generation, minimal memory config |
| P0.5-1 | Author-preserving debounce and batch flush |
| P0.5-2 | Trigger sequence and same-user interruption |
| P0.5-3 | Split reply and serialized per-tenant sending |
| P0.5-4 | Reply/quote segments and platform fallback |
| P5 | Planner first, then `reply_likelihood` + `dynamic_gate` |

P0.5 stops after quoting. It does **not** implement proactive replies. Ordinary group messages only write history; `ai_router` handles @, persona name, and private chat.

P5 is explicit work, not an automatic continuation. Do not implement the proactive gate until Planner samples exist or the user asks for the observe-only phase.

## Non-Negotiable Invariants

- `tenant = session_id`. Every read, update, delete, and cache key is tenant-scoped. Never merge groups or private chats.
- Business code must not import platform-specific Bot, Event, or Message types. Put platform differences in `adapter/platforms/`.
- Store naive UTC in SQLite. Use `utils/tz.py` for Shanghai display. Do not treat naive UTC as local time.
- Register matchers through `Plugin` with the literal `P` and AST-recognizable forms.
- Read configuration through `ConfigManager` at use time; preserve hot reload. Do not cache config copies.
- Use `utils/cache.py` for in-process caches. Prefix tuple keys with the domain and invalidate at the write entry point.
- Use explicit `CREATE TABLE/INDEX IF NOT EXISTS` and column checks for chat migrations. Do not rely on `exec_list` swallowing exceptions.
- Generated text is not visible history. Only successfully sent turn items with `visible=true` feed the next turn.
- Initial DB failure degrades long-term memory reads/writes and learning, but must not disable basic chat.
- A P5 gate is sampled once per event. If it passes, `_context_rule` returns false and `_ai_fallback_rule` bypasses `_match_context`; otherwise `_handle_context` writes history. Never let both paths write the same user message.
- Focus 接管聊天时，路由登记后直接返回：普通防抖、规划器与 proactive gate 都不再参与；回复仍由该聊天自己的回复模型生成并发送到自己的目标。
- Proactive turns use only read-only tools (`query_memory`, `query_person_profile`, `web_search`); admin tools are rejected for the whole batch if any message is proactive.
- Do not invent cooldowns or caps. There is no fixed 5-minute/60-second/5-per-10-minute/hourly limit. Use only MaiBot's `talk_value` window budget and `no_action_backoff`.

## MaiBot Code Migration

Copying and migrating MaiBot code is allowed for this personal project. Preserve a source comment naming the MaiBot file and commit. Adapt to FxBot boundaries instead of rewriting algorithms for appearance.

Direct-copy candidates:

- `src/maisaka/turn_trigger/reply_likelihood.py` → `chat/behavior/reply_gate.py`;
- `src/maisaka/turn_trigger/dynamic_gate.py` and `idle_backoff.py` → same module;
- Planner prompts, parsers, and scheduling structure → `chat/planner.py` and debounce;
- expression, jargon, typo algorithms and prompts → the corresponding chat modules;
- A_Memorix memory kernel → vendored at `chat/amemorix/`; do not reintroduce the removed `chat/memory/extract.py` pipeline.

Do not copy MaiBot's platform adapters, OneBot/QQ sending, or global config loader. Reconnect those through FxBot's `adapter/platforms/*`, `ConfigManager`, permission model, and repository layer.

Do not reintroduce classical willing. MaiBot removed it in `1f91967d2`; P5 uses Planner labels plus the latest gate.

## Workflow

1. Pick one item from section 22 and state its acceptance criteria.
2. Locate the current call chain and neighboring tests with `rg`.
3. Write or extend a `unittest` test under `tests/chat/` before or with the change.
4. Make the smallest implementation change that satisfies the phase item.
5. Run the focused test, then `ruff check` and `ruff format --check` on changed Python files.
6. If `console/web/src/` changed, run the frontend build and include `console/web/dist/`.
7. Report changed files, tests run, failures, and any remaining limitation. Do not claim verification you did not run.

Implement items one at a time, but commit a whole phase as a single commit once all its items pass verification. Do not commit sub-items separately. Do not reformat untouched legacy files.

## Verification Matrix

- **P0-1**: OneBot12 `id` vs `message_id`; duplicate delivery; cross-bot tenant; quoted target vs trigger ID.
- **P0-2**: fresh DB, repeated migration, broken migration, missing column; UTC round-trip; DB-failure startup.
- **P0-3**: restart continuity; clear-then-restart; history trim; late image description after recall; cross-tenant read failure.
- **P0-4**: model failure, empty response, first-segment failure, duplicate send; generated-but-not-sent excluded from history; clear during generation.
- **P0-5**: clear clears memory/DB/in-flight work; `max_context_size` hot reload; invalid config does not crash startup.
- **P0.5-1**: same-author burst, cross-author, window reset, 30-second cap, forced flush, no duplicate user append.
- **P0.5-2**: A1→A2 aborts A1; A1→B1 lets A1 finish; partial send is not rolled back.
- **P0.5-3**: long text, first/middle segment failure, concurrent turns, no interleaving per tenant.
- **P0.5-4**: quote support, unsupported platform, construction failure, text fallback.
- **P5**: Planner first-round labels; gate observe-only before enforce; @ bypass; one sample per event; window statistics.

## Anti-Patterns

- Adding proactive behavior before P5.
- Reintroducing `willingness.py`, `willingness_enabled`, or `talk_frequency`.
- Adding fixed proactive cooldowns or caps that MaiBot does not have.
- Recording an ordinary group message in both `_handle_context` and `process`.
- Using `finish(text)` as the send path without recording send status.
- Adding a sidecar, vector service, graph DB, Redis, or unused Protocol.
- Copying MaiBot platform code instead of using the adapter.
- Sending exception text or internal paths to customers.

## Maintenance

Update this skill only when phase boundaries, architecture, migration policy, or verification entry points change. Do not update it for ordinary feature work, command additions, or line-number changes.
