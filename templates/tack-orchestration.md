---
name: tack-orchestration
description: Run an explicitly requested development goal with the project-local Tack orchestration harness. Use only in projects where this optional harness is installed; global Tack records tasks and sessions without orchestrating agents.
---

Use `./.tack-orchestration/bin/tack-orchestration` from the project root (called HARNESS below). It stores tasks, sessions, decisions and results in the existing Tack project DB. Never create another task ledger or install this skill globally.

For an explicit orchestration request, inspect `tack status` and `HARNESS goal list` and reuse matching work. For ordinary manual work use global `tack` recording only; this skill does not authorize starting a team automatically.

For a new employee-team workflow, use `HARNESS team create 'BIG GOAL'`, then `HARNESS team run GOAL_ID --version N` to let the planning employee work read-only. The `/team` Dashboard creates and starts this planning request together. Employees own jobs and may ask colleagues concrete questions; a reply re-queues the original job. User questions always wait for an actual user answer. Do not start a separate agent outside this controller.

Inspect `HARNESS team show GOAL_ID`. Proposed jobs stay awaiting_approval until `HARNESS team approve GOAL_ID --version N`; this binds employee settings, instructions, skills and explicit file scope. Then `HARNESS team run GOAL_ID --version N` schedules runnable jobs by dependencies. Only an implement-type employee doing an approved task gets workspace-write. Questions and reviews are read-only. Employee conversations are isolated by goal, employee and read/write capability. Employee configuration or skill changes invalidate implementation approval.

For user replies use `HARNESS team reply GOAL_ID JOB_ID --json FILE` with current job `version` and actual `body`. Direct assignment uses `HARNESS team assign GOAL_ID --json FILE` with current goal version, title, employee, instructions, files, depends_on job IDs and unique client_id. A new assignment still needs scope approval. `team message` accepts current goal version, kind note/question, employee, body and unique client_id; note never invokes a model. Run explicitly to process queued questions. `team retry GOAL_ID JOB_ID --json FILE` with current job version requeues an interrupted/failed job after its execution has stopped.

Completed implementation goes to an independent verify employee. Autopilot may deliver in-scope revision feedback to its original owner, up to the configured limit; newly proposed work always needs user approval. The controller runs approved project verification commands after all jobs finish. The goal stays awaiting_acceptance until the user explicitly calls `HARNESS team accept GOAL_ID --version N`. Neither a completed employee turn nor a review verdict authorizes goal completion or remote actions.

Existing step-based goals retain `HARNESS goal list/show/start/send` and `/goals`. They are preserved without converting their data. New teams do not use next_step as a scheduler.

Team settings are project-scoped: `HARNESS settings show` and `HARNESS settings update --json FILE`. Each role has a stable ID, name, kind (`plan`, `implement`, `assess`, `verify`), model, instructions, and explicit SKILL.md paths. Only implement-type roles get workspace-write. The coordinator `plan` ID is retained; include implement and verify types. Existing records retain original role IDs. Do not delete roles still needed by unfinished goals.

For legacy step-based goals, approved steps execute in order with actual prior role results. Assigned skill contents are read for that role and their paths/hashes are recorded. Evaluators return factual passed/summary plus an optional structured handoff. With Autopilot enabled, an in-scope revision to an earlier configured role can restart that role and all subsequent evaluation/verification steps, up to max_rounds. A user question pauses until an actual answer. Time and token limits also stop execution; response-boundary token counting can exceed the cap within one response.

`HARNESS goal pause ID --version N` interrupts only owned processes. Missing heartbeat is not proof of exit. Confirm the original process stopped before `HARNESS goal recover ID --version N --confirmed-stopped`, then explicitly resume. Never retry another tool's externally owned session.

Use global `tack bind ACTUAL_THREAD_ID TASK_ID` for passive session linkage; compare-switch with `--from-task OLD_ID`. Hook metadata never implies task completion. All task completion requires actual verification. Existing Codex App/Orca sessions are observed, not remotely controlled by this harness.

Goal-specific role guidance and recommended skills stay on that goal, including existing roles. They never become instructions for other goals. Explicit acceptance and verification commands survive replanning; the coordinator can still ask questions. Blocking user questions take precedence over passed=true. Role results are preserved and handed on without silent truncation. Approval is bound to the current plan, roles and workspace; changing that contract requires explicit approval again. Run from the goal's original checkout.

직원 팀은 `max_parallel` 범위에서 독립 업무를 동시에 수행한다. 동일 직원은 한 업무씩 처리하고, 선행 업무·승인·답변을 기다리는 업무는 DB 큐에 남긴다. 각 업무는 격리된 복사 작업 공간에서 실행하며 승인 파일과 기준 해시가 일치하는 변경만 통합한다. 충돌 산출물은 보존하고 사용자 확인 없이 덮어쓰지 않는다. 목표 실행 요청은 지속 FIFO 큐로 처리하며 대시보드는 실행의 필수 조건이 아니다.

`/team`은 팀 단체방과 직원 개인방으로 목표별 대화를 구분한다. 개인방 질문은 읽기 전용 업무이며 구현 권한을 부여하지 않는다. 질문·답변은 원래 대화방에 보존하고 후속 질문은 같은 목표·직원·방의 세션만 재사용한다. 개인방 구분은 사용자별 접근 권한 경계가 아니다.
