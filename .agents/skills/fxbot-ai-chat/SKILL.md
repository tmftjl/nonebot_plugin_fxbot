---
name: fxbot-ai-chat
description: Development and review workflow for FxBot chat, history persistence, memory, planning, reply timing, and sending. Use for the repository chat subsystem, not generic NoneBot questions or unrelated subplugins.
---

# FxBot AI Chat Development

Read `../fxbot/SKILL.md` first. Current source code is the implementation source of truth. Design notes, migration comments and this skill do not prove that behavior is implemented or enabled. Do not revive historical P0/P0.5/P5 restrictions or require following an old design document.

## Investigation

- Follow the affected path from `chat/router.py` through `chat/service.py` and its actual callees. Inspect ordinary planning and Focus separately when both are affected.
- Compare configuration defaults, console schema, validation and business reads. Distinguish saved runtime values from defaults, and exposed fields from effective behavior.
- Separate chat history and learning storage under `chat/memory/` from long-term memory under `chat/amemorix/`. Inspect the host bridge before changing the vendored kernel.
- For MaiBot comparisons, inspect the specified checkout and its callers. Similar names and copied algorithms do not establish full behavior parity.

## Boundaries to Preserve

- Keep message, event, turn and reply-target identity distinct. Check deduplication and adapter identity extraction when changing ingress.
- Bind history ownership and sending targets to the actual session. Explicit Focus, learning-sharing and memory-retrieval scopes permit configured shared reads; they do not permit silently mixing all chats. Inspect each feature's scope and authorization.
- Preserve the distinction between generated output and successfully sent visible history. Check cancellation, partial sends and clearing against in-flight turns.
- For batching and tools, trace authors, runtime events and allowed tools through discovery and execution. Do not assume a design document's batch authorization or read-only guarantees are implemented.
- Inspect Focus routing before changing ordinary debounce or proactive gates; avoid duplicate reply paths and duplicate history writes.
- Plugins derive chat namespaces through `chat/session_hooks.py`, not by patching callers. Resolve once per event, preserve platform/scene/target identity, and reject resolver failures rather than falling back to an unrelated session. Session policies must be honored by Focus and both ends of learning sharing; they do not change A_Memorix's own-session/public-knowledge semantics. See `docs/chat-session-hooks.md` for the extension contract and verify default behavior, dynamic policy restrictions and event-local consistency when changing it.
- Preserve ConfigManager hot reload and platform adapter boundaries from the core skill.
- Use `chat/hooks.py` for request/send guards, per-generation persona/prompt/reply-text filters and lifecycle observers; the contract is in `docs/chat-hooks.md`. Pass immutable identity snapshots, keep tool guards deny-only at discovery and execution, filter reply text before splitting/persistence, and never let observers change delivery truth or prevent core cleanup. Verify ordinary chat, Focus, subagent tool execution, partial sends and cancellation when changing these extension boundaries.

## Implementation and Verification

Make the smallest change for the current request. Do not infer unfinished phases from historical plans. Preserve source attribution when migrating MaiBot code and adapt integration boundaries to FxBot.

For MaiBot parity work, implement the current target contract directly. Do not add data migrations, historical-data rewriting, old-mode routing, legacy payload parsers or checkpoint upgrades. Remove superseded paths and tests that exist only for old modes; keep verification of the current conversation behavior and session isolation. Required host and platform adapters remain integration boundaries, not alternate conversation modes.

Choose focused existing tests for affected behavior. For substantive behavior changes, cover applicable regressions: restart hydration, duplicate ingress, interruption, cross-author batching, partial sends, clearing during generation, explicit sharing scopes and tool authorization. Derive timing values and expected semantics from current requirements and code, not frozen examples in this skill.

Run applicable core-skill checks for changed code. Documentation-only work needs reference and diff checks, not an application build. Report actual verification and separate confirmed findings from untested hypotheses.

## Maintenance

Keep durable boundaries and verification methods here, not configuration inventories, implementation snapshots, phase plans or historical line numbers. Correct contradictions with current code and user requirements when discovered.
