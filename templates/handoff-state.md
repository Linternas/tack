---
name: handoff-state
description: Save or resume unfinished work using the global tack CLI and shared task/session database. Use for handoff, pause, expected context compression, long-running continuation or an explicit progress record request; do not create parallel Markdown state files.
---

# Unified Handoff State

Use Tack for checkpoints shared by CLI and Dashboard. Follow the global AGENTS task policy; keep repository product requirements, policy, API contracts and architecture/design as linked sources. Work plans, TODOs, steps, QA execution/results, reviews and completion records belong in the same task DB; never create plans/done/working files as an alternative.

1. Run `tack status`; select the relevant task and read `tack resume TASK_ID`. If none exists, create it with `tack task add 'TITLE'` and retain the returned ID/version. Do not resurrect completed work from historical notes.
2. Save a concise JSON object in an allowed temporary file. Include only `goal`, `decisions`, `changed_files`, `verification`, `blockers`, `next_action`, and `status` (`active`, `blocked`, `completed`). Preserve meaningful repository paths and exact remaining action; distinguish historical validation from current validation. Do not include secrets, full conversations or raw logs.
3. New session: `tack checkpoint TASK_ID --json /tmp/checkpoint.json`. Existing session: include its current `version` in JSON and run `tack checkpoint TASK_ID --session RUN_ID --json /tmp/checkpoint.json`. Reuse the matching task/thread/checkout session. CLI records current workspace, branch and available thread ID; `agent`/`thread_id` can be specified when needed.
4. Preserve JSON input on failure. For 409 read latest state and reconcile. Do not retry by automatically substituting a newer version. For unavailable storage report the failed save; never create a second DB or Markdown fallback. Explicit local initialization is for unregistered projects; existing storage changes require a deliberate migration.
5. Close the session with `status=completed` when it ends; the store clears its next action. If the requested task is also complete, separately update its latest version using `tack task update TASK_ID --json /tmp/task-update.json` with `status=done`, `resolution` and a factual `note`. Session closure never implies QA passed. Completion of an analysis task never implies the proposed implementation is complete.
6. Briefly report the saved task/session ID when relevant. No state document or per-turn checkpoint is needed for routine answers.

For commands and storage details use `tack --help` or the README in the installed tool source; obtain the actual launcher path with `command -v work` rather than assuming a machine path.
