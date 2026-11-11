---
name: handoff-state
description: Save or resume unfinished work using the global tack CLI and shared task/session database. Use for handoff, pause, expected context compression, long-running continuation or an explicit progress record request; do not create parallel Markdown state files.
---

# Unified Handoff State

Use Tack for work state and repository documents for durable product requirements, policies, API contracts and architecture.

1. Search with `tack search "task keywords"`, then read the matching unfinished task with `tack resume TASK_ID`. A known task ID can be resumed directly. Use `tack status` for counts and a bounded recent list. Create a task only after checking matching records. Completed tasks are references, not unfinished work.
2. Read required scope and evidence through `tack record list TASK_ID --section SECTION` or `--kind checkpoint`, then `tack record show RECORD_ID`. Follow `next_record` for remaining parts; `--full` explicitly retrieves the complete record or legacy task. Fields listed in `truncated_fields` are previews.
3. Keep current task state short. Append a new substantial decision, verification result or note with `tack record add TASK_ID --kind verification --text 'TEXT'` or `--file FILE`. Reference record IDs instead of copying evidence into several fields. Preserve unexecuted or failed checks as facts. Exclude secrets, full conversations and raw tool logs.
4. At a meaningful checkpoint write a concise JSON object in an allowed temporary file. `goal`, `decisions`, `changed_files`, `verification`, `blockers`, `next_action` and `status` are strings. Use newline-separated text for file lists. Status is `active`, `blocked` or `completed`. Include new facts and references needed for resumption. Routine answers need no checkpoint.
5. New session: `tack checkpoint TASK_ID --json FILE`. Existing session: include its current `version`, then `tack checkpoint TASK_ID --session RUN_ID --json FILE`. Reuse the matching task/thread/checkout session. The CLI supplies workspace, branch and available thread identity. Preserve input on failure; on 409 reread current state and reconcile the change. Report unsaved state on unavailable storage and preserve the existing work database.
6. Reconcile the work you own before handoff or final response. Reuse the task through retries and reviews. For remaining work, record the next action or unblock condition and use the appropriate pending state when actual execution stops. Resolve only blockers you verified, preserve history and leave other sessions’ tasks intact. Archive cancelled, superseded or duplicate work only with a confirmed reason and any successor ID. Pass the task ID and this checkpoint to the receiving session.
7. Close the session with `status=completed` when it ends. Separately complete the task with its latest version, `status=done`, a concise `resolution` and actual scoped verification. QA completion requires its actual result. A session ending, or an analysis finishing, does not establish implementation or QA success.

Use `tack --help` for command details. Work plans, progress, QA and handoffs belong in Tack rather than parallel Markdown ledgers or `~/.codex/work-state`.
