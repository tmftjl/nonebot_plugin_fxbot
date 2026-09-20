---
name: fxbot
description: Core development standards for FxBot (nonebot_plugin_fxbot). Use when changing or reviewing framework, startup, adapter, permission, configuration, database, AI, system-control, console, membership, or delivery behavior. Do not use for generic NoneBot questions or isolated plugins/ work without repository context.
---

# FxBot Development Standards

This directory is both a Git repository root and a plugin inside the outer NoneBot2 project. The repository has a framework layer and a built-in subplugin layer under `plugins/`; built-in subplugins are not independent NoneBot plugins.

Source code is the sole source of truth for implementation details. Before editing, use `rg` to locate the current entry points, callers, and nearby implementations. Do not rely on skills for line numbers, counts, command inventories, or version snapshots.

## Skill Scope

This skill covers the framework, console, membership system, and repository-wide standards. When changing a built-in subplugin under `plugins/`, also read `../fxbot-subplugin/SKILL.md`; cross-cutting work must follow both skills.

Do not split standards into more skills based on source directories. Discover concrete interfaces, commands, configuration fields, and page structure from the source when doing the work.

Explicit user instructions define the requested scope and outcome. Apply this skill to implementation choices unless the user explicitly asks to change one of these repository standards; never use the skill to expand authorization or scope.

## Standard Workflow

1. Inspect repository status and the relevant source call chain with `rg`.
2. Identify the applicable invariant sections below and the smallest change point.
3. Implement locally, preserving existing compatibility boundaries.
4. Run focused validation, then the repository checks required by the change.
5. Report changed files, verification performed, and any remaining limitation. Do not update this skill for ordinary feature work.

## Shared Hard Constraints

1. Register every subplugin matcher through the `Plugin` wrapper using the literal variable `P`, including dynamic factories such as `P.on_notice(...)`. Use a string literal for `name=` and direct `PermLevel.X` and `PermScene.X` expressions for `level=` and `scene=`; otherwise the permission AST scan can silently miss entries.
2. Business code must not import platform-specific Bot, Event, or Message types. Keep platform differences in `adapter/platforms/`; use `selfBot`, `bind_bot`, unified session dependencies, or forwarding functions exported by `adapter` in business code.
3. Use the shared asynchronous client from `utils/http.py` for external HTTP and helpers from `utils/paths.py` for runtime paths. Do not hardcode package paths or data directories.
4. Register and read plugin configuration through `ConfigManager`. Do not introduce a parallel NoneBot or Pydantic configuration system. Preserve hot reload by reading configuration at use time instead of caching copies in business modules.
5. Use `utils/tz.py` for ordinary business time. Membership is the explicit exception: storage and decisions use UTC, and naive datetimes returned by SQLite are interpreted as UTC. Never treat naive UTC as Shanghai local time.
6. Read and write Chinese or other non-ASCII files explicitly as UTF-8. Python modules, classes, and functions use one-line Chinese docstrings; comments explain why.
7. Follow nearby code and `ruff.toml`: start with `from __future__ import annotations`, use built-in generics, PEP 604 unions, double quotes, and a 120-column limit. `plugins/memes/` is vendored code; preserve its local style.
8. After changing `console/web/src/`, build the frontend and include the corresponding `console/web/dist/` artifacts.
9. `on_shutdown` hooks run in reverse registration order. Before changing restart, exit, or cleanup behavior, verify both registration order and exception interruption semantics.
10. Never expose runtime errors directly to customers. Log them with context and stack traces, then end Bot interactions silently. HTTP APIs return only generic errors without internal details. Never include `str(exc)`, exception types, stack traces, upstream responses, or internal paths in customer-visible output.

Expected business validation and operation rejections are not runtime errors. They may use short, predefined messages that help customers correct their input, but those messages must not come from exception objects. Silent handling does not mean swallowing errors: never use a bare `except` or an unlogged `except Exception: pass`.

## Startup-Order Invariants

Treat `bootstrap.py` as the source of truth rather than duplicating its full sequence here. Preserve these relationships when changing startup:

- Configuration is initialized before modules that depend on it.
- SQLModel models enter metadata before table creation; loading a subplugin model by file must not execute the package entry point and register matchers early.
- The membership event preprocessor is registered before business matchers.
- The AI fallback router is registered before built-in subplugins are loaded.
- Built-in subplugins are loaded last.

Classify new startup failures by impact: abort startup, fail closed, or log a warning and degrade. Do not swallow them indiscriminately.

## Framework Constraints

- `PlatformAdapter` defines the platform capability boundary. Implement platform differences only in `adapter/platforms/` and register them through the existing registry. `selfBot` requires an event context; background tasks must retain and bind a Bot explicitly.
- `UnsupportedCapability` means the platform lacks a capability and must be handled separately from unexpected runtime failures. When adding a platform, check event reading, message construction, target extraction, sending, and message statistics together.
- `Plugin` is the only matcher registration entry point for subplugins, and its dynamic proxy supports every NoneBot `on_*` factory. Permission changes must account for both initial AST scanning and runtime defaults.
- `ConfigProxy.load()` supports hot reload. `clean_extra=True` removes keys outside the default structure; only an empty dictionary default preserves arbitrary mapping entries.
- `@with_session` owns the default session and commit. Pass a session explicitly only to share a transaction. `create_all` does not migrate existing tables; use the repository migration mechanism for schema changes and keep migrations idempotent.
- Extend AI providers and tools through the existing registries. Tool parameter schemas must match function signatures. Registration timing is part of AI fallback behavior; priority alone is insufficient.
- Global hooks and monkey patches used by restart or message statistics must be idempotent. Verify normal shutdown, interruption by errors, and repeated installation.

## Console Constraints

- Put backend routes in `console/routes/` and mount them explicitly from `console/server.py`. Except for deliberately public health checks and static assets, APIs use router-level Bearer authentication.
- Follow existing interfaces in the same domain for requests, responses, and error handling. Do not break compatibility merely to make old and new endpoints look uniform. Log runtime errors server-side and return a generic error without exception details in `detail`. Use the adapter rather than private platform APIs.
- The backend schema and `SYSTEM_DEFAULTS` jointly define system configuration forms; subplugin forms are discovered from `ui_schema.py`. When adding a field, validate defaults, schema, business reads, and merging with existing configuration.
- Before integrating frontend work, inspect the current navigation, API client, types, and neighboring pages. Do not freeze a router or state-management assumption into this standard. Use the existing API client and design tokens.
- Do not change the frontend when adding a plugin configuration field that the existing schema can represent. After changing `console/web/src/`, run type checking and a production build, then include `dist/`.

## Membership Constraints

- Membership gates fail closed: deny ordinary group business messages when the database is unavailable, a query fails, or the record is unknown. Renewal and expiry-query recovery paths must remain reachable during failures and for unregistered or expired groups.
- Register the gate preprocessor before business matchers. When adding a command intended for restricted groups, update and verify the gate escape rules.
- Membership decision caching has no TTL. Precisely invalidate the cache after creating, extending, changing, or deleting a group record; reload it after batch jobs. Read-only operations, reminders, and renewal-code generation do not refresh group cache.
- SUPERUSER and `system.bot_admins` exemptions are checked before group cache, so changes to those settings do not require cache invalidation.
- Membership storage and decisions use `models.utc_now()` and the existing UTC conversion rules, not Shanghai-time conversion helpers. Customer-facing dates use the shared membership formatter.
- Extend active memberships from their existing expiry; extend expired or new memberships from the current UTC time. Renewal-code validation, usage updates, extension, and audit records must share consistent transaction semantics.
- `category="system"` permissions differ from ordinary subplugins. Keep administrative commands protected by SUPERUSER, and do not accidentally block self-service renewal through blanket permission changes.
- One group failure in an expiry job must not stop the batch. Send notifications, leave groups, and perform console messaging through the adapter, with explicit handling for offline Bots and unsupported capabilities.

## Working Method

- Read the real call chain and neighboring code before making a focused change. Do not refactor unrelated coexisting styles merely for consistency.
- Put reusable capabilities in the framework and business differences in subplugins. Introduce an abstraction only when it removes real duplication or isolates change.
- Verify critical invariants first for nontrivial work, then perform the smallest relevant manual regression pass.

## Verification and Commits

Run the relevant checks from the repository root:

```powershell
ruff check .
ruff format --check .
```

For matcher changes, verify triggering, permissions, scene, `priority`, `block`, and display names. For configuration changes, verify default merging and hot reload. Startup, adapter, console, and membership changes should also cover:

- startup order, repeated initialization, and each failure class;
- at least one OneBot platform, one structurally different platform, and an unsupported-capability path;
- console access with no token, a wrong token, and a correct token, plus loading the built artifacts from `/fxbot`;
- membership gating disabled and enabled, exemptions, unregistered/active/expired groups, database failure, immediate cache invalidation, renewal time boundaries, and offline Bots.

Use a single-line Chinese commit subject without prefixes such as `feat:`. For nontrivial changes, explain the reason, impact, and actual verification in the commit body.

## Skill Maintenance Boundary

Keep only this core standard and the subplugin standard in `.agents/skills/`. `.claude/skills/` may only contain local junctions, never copies.

Do not update skills for ordinary feature work, command or endpoint changes, dependency patch/minor upgrades, or source line changes. Update a skill only when:

- an architectural boundary or extension mechanism changes;
- a stable invariant related to silent failure, data safety, or security changes; or
- a long-term development entry point, verification tool, or delivery requirement changes.

Document how to decide and verify the rule. Do not copy source inventories, implementation walkthroughs, current counts, versions, or line numbers into skills.
