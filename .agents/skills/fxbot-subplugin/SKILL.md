---
name: fxbot-subplugin
description: Development standards for built-in FxBot subplugins. Use when adding or changing commands, message handlers, configuration, resources, tasks, persistence, or AI tools under plugins/ within this repository. Do not use for generic plugin-development questions without FxBot source context.
---

# FxBot Built-in Subplugins

Read `../fxbot/SKILL.md` first. Built-in subplugins are loaded by FxBot and are not independent NoneBot plugins. Reuse framework capabilities instead of building parallel infrastructure.

Explicit user instructions define the requested scope and outcome. Apply this skill to implementation choices unless the user explicitly asks to change a repository standard; never use it to expand authorization or scope.

## Before Editing

Use `rg` to find an existing subplugin with similar responsibilities, then read its entry point, matchers, configuration, and resource handling. Follow the closest implementation for directory layout and module boundaries rather than copying a fixed template from this skill.

The framework discovers extension points by directory convention:

- Startup orchestration loads subplugin packages.
- `models.py` is located by file and imported dynamically before table creation; it must not register matchers as an import side effect.
- The console scans for and dynamically imports `ui_schema.py`.
- The help plugin scans and reads `help.json`.

Discovery by scanning does not mean “never imported.” Keep both `models.py` and `ui_schema.py` import-safe.

## Matchers and Permissions

- Define `P = Plugin(...)` and register every matcher through `P.on_*`, including notice, request, and other dynamically proxied factories. Do not call NoneBot's native `on_*` functions directly.
- Follow the AST-recognizable literal forms required by the core standard. Give each command a stable internal `name` and a customer-readable `display_name`.
- If multiple modules construct `P` for one subplugin, keep the plugin name and default permissions identical. Whether to share one `P` or construct it per module should follow that subplugin's existing organization.
- Inspect neighboring matchers before choosing `priority`, `block`, and rules so the new matcher does not preempt automatic parsing, passive events, or the AI fallback.
- Prefer injected unified session data in handlers. Use raw Bot or Event objects only when the adapter boundary genuinely requires them.

## Configuration, Data, and Resources

- Defaults, console schema, and business reads form one contract. Validate all three when adding a setting, but do not update the skill for routine setting changes.
- Register configuration with `ConfigManager` and read it at use time. Use the framework's empty-dictionary convention for dynamic mappings.
- Put writable state under `data_dir()`, disposable data under `cache_dir()`, and package resources relative to their module. Never write runtime data into source directories.
- Use the existing SQLModel and `@with_session` stack for data requiring queries, transactions, or relationships. Place models where startup can discover them before database initialization.

## External Capabilities and Background Tasks

- Use the shared asynchronous client for network requests and the adapter for platform operations. Handle `UnsupportedCapability` explicitly; catch other exceptions only when the code can recover, translate semantics, or add useful context.
- Log runtime errors with context and stack traces, then end the matcher silently. Do not send failure reasons to customers or interpolate exception text into messages. Only expected business branches such as missing parameters, invalid formats, or insufficient permissions may send predefined guidance.
- Release image-rendering and browser resources on both success and failure paths. Preserve a lightweight fallback when the feature already defines graceful degradation.
- Background loops must support idempotent startup, cancellation, and shutdown cleanup. A single failed iteration must not silently kill a long-running task. Follow neighboring subplugins when choosing a scheduling mechanism.
- Register AI tools through the `chat.tools` registry and keep parameter schemas aligned with function signatures. High-risk tools validate scene, permission, and parameter boundaries themselves.

## Style and Boundaries

- Subplugins do not import concrete platform implementations and do not implement membership gating themselves.
- Avoid direct dependencies between subplugins. Move reusable capability into the framework and use existing convention files for discovery-oriented collaboration.
- `plugins/memes/` is vendored code; preserve its local style and avoid opportunistic cleanup.

## Verification Focus

- Trigger every added or changed matcher and verify permissions, scene, display name, priority, and blocking behavior.
- For configuration changes, verify existing-config merging, extra-key cleanup, console rendering, and hot reload.
- For background tasks, verify repeated startup, one failed iteration, clean shutdown, and an offline Bot.
- For platform capability changes, verify both supported and unsupported paths.
- For new models, verify both a fresh database and migration of an existing database.

Done means the change is narrowly scoped, uses existing framework boundaries, passes the relevant checks, and does not expose runtime error details to customers. Do not modify this skill for ordinary subplugin feature work.
