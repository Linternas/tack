---
name: tack-orchestration
description: Run an explicitly requested development goal with the project-local Tack orchestration harness. Use only in projects where this optional harness is installed; global Tack records tasks and sessions without orchestrating agents.
---

Use `./.tack-orchestration/bin/tack-orchestration` from the project root (called HARNESS below). It stores tasks, sessions, decisions and results in the existing Tack project DB. Never create another task ledger or install this skill globally.

For an explicit orchestration request, inspect `tack status` and `HARNESS goal list` and reuse matching work. For ordinary manual work use global `tack` recording only; this skill does not authorize starting a team automatically.

`HARNESS goal draft 'BIG GOAL' --json '{}'` starts read-only goal interpretation. The coordinator inspects the project, recommends necessary role agents and installed skills, and asks material questions. `waiting_answer` is not approval or completion. Send the user's actual answer with `HARNESS goal send ID --json FILE`, where FILE contains latest `version`, `kind:"answer"`, `body` and unique `client_id`. Never answer on the user's behalf unless their explicitly selected Autopilot policy allows a reversible assumption. Missing skills and authority decisions require disclosure.

Read the proposed roles, skills, completion conditions, file scope and real verification commands in `HARNESS goal show ID` or the optional chat Dashboard. Initial implementation always needs explicit approval via `HARNESS goal start ID --version N`. Autopilot does not approve an initial plan or authorize scope expansion, remote Git writes, deployment or external messages.

Team settings are project-scoped: `HARNESS settings show` and `HARNESS settings update --json FILE`. Each role has a stable ID, name, kind (`plan`, `implement`, `assess`, `verify`), model, instructions, and explicit SKILL.md paths. Only implement-type roles get workspace-write. The coordinator `plan` ID is retained; include implement and verify types. Existing records retain original role IDs. Do not delete roles still needed by unfinished goals.

Approved steps execute in order with actual prior role results. Assigned skill contents are read for that role and their paths/hashes are recorded. Evaluators return factual passed/summary plus an optional structured handoff. With Autopilot enabled, an in-scope revision to an earlier configured role can restart that role and all subsequent evaluation/verification steps, up to max_rounds. A user question pauses until an actual answer. Time and token limits also stop execution; response-boundary token counting can exceed the cap within one response.

`HARNESS goal pause ID --version N` interrupts only owned processes. Missing heartbeat is not proof of exit. Confirm the original process stopped before `HARNESS goal recover ID --version N --confirmed-stopped`, then explicitly resume. Never retry another tool's externally owned session.

Use global `tack bind ACTUAL_THREAD_ID TASK_ID` for passive session linkage; compare-switch with `--from-task OLD_ID`. Hook metadata never implies task completion. All task completion requires actual verification. Existing Codex App/Orca sessions are observed, not remotely controlled by this harness.

Goal-specific role guidance and recommended skills stay on that goal, including existing roles. They never become instructions for other goals. Explicit acceptance and verification commands survive replanning; the coordinator can still ask questions. Blocking user questions take precedence over passed=true. Role results are preserved and handed on without silent truncation. Approval is bound to the current plan, roles and workspace; changing that contract requires explicit approval again. Run from the goal's original checkout.
