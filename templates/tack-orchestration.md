---
name: tack-orchestration
description: Run an explicitly requested development goal with the project-local Tack orchestration harness. Use only in projects where this optional harness is installed; global Tack records tasks and sessions without orchestrating agents.
---

Use `./.tack-orchestration/bin/tack-orchestration` from the project root (called HARNESS below). It stores tasks, sessions, decisions and results in the existing Tack project DB. Never create another task ledger or install this skill globally.

For an explicit orchestration request, inspect `tack search "relevant keywords"` and `HARNESS goal list` and reuse matching work. For ordinary manual work use global `tack` recording only; this skill does not authorize starting a team automatically.

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

목표별 위임은 기본 수동이다. 승인된 요구사항·역할·파일·계약·검증·한도 범위만 자동 요청할 수 있다. 코드·계약 변경은 work_request, 실제 계약 산출물은 contract_proposal, 지정 계약 소비 검토는 contract_review 구조로 반환한다. 계약 확정/구현 검증/통합 검증 조건을 구분하고 사용자 중단·계약 버전·실행 시도 ID를 재개와 통합 전에 검사한다. 동료 메시지·공유 문서·스킬은 권한을 확대하지 않는다. 위임 철회·만료·미수집 사용량·진전 없는 재시도·순환 요청은 자동 진행을 중단한다.

## 질문과 작은 작업

저장소 관례와 같은 목표에서 받은 답변을 먼저 확인한다. 되돌릴 수 있는 구현 선택은 간단한 기본값으로 정하고 가정을 제안·결과에 밝힌다. 핵심 제품 결정, 요구사항 충돌, 데이터 손실·비용·외부 부작용·권한처럼 실제 진행을 막는 사항만 추천안과 함께 묶어 질문한다. 필요한 승인은 추측하거나 생략하지 않는다.

작은 CLI·수정은 구체적인 파일과 검증 명령을 가진 최소 구현 업무로 제안한다. 선택적인 역할·스킬·기획 문서를 실행 조건으로 요구하지 않는다. 구현 결과의 독립 검토는 컨트롤러가 배정한다. 수동/계획 승인 후 자동 실행은 범위 승인을 기다린다. Autopilot은 목표에 위임된 프로젝트 권한 안의 최초 제안을 컨트롤러가 자동 승인한다. 최종 완료 확인은 유지한다.

질문 카드에서 제출한 공개 답변은 같은 목표의 후속 업무에 전달한다. 개인방 답변은 같은 직원·대화방에서만 재사용한다. 이미 답변한 동일 질문(대소문자·공백 차이 포함)은 한 번만 재검토하고 계속 반복하면 중단 사유를 기록한다. 표현이 다른 질문의 의미적 중복까지 자동 판별하는 기능은 아니다.

## 실행 모드와 프로젝트 권한

`execution_mode`는 manual(업무·수정 승인), approved(계획 승인 후 검토·수정 자동), autopilot(프로젝트 권한 안의 계획·업무까지 자동)이다. 기존 autopilot boolean은 approved에 대응한다. 모드는 권한을 확대하지 않는다. 목표 등록 당시의 수정 허용·보호 경로·삭제·검증·네트워크 권한과 현재 프로젝트 제한을 모두 지킨다. 시간·토큰·동시 실행·재작업 상한은 계속 적용한다. 새 Autopilot은 기존 목표에 자동으로 소급 적용하지 않는다.

자동 승인할 검증 명령은 permissions.check_prefixes의 argv 접두사로 제한하며 목록 밖 명령은 사용자 계획 승인이 필요하다. run_checks=false이면 직접 승인해도 검증을 실행하지 않는다. Git·외부 부작용은 자동 허용하지 않는다. 파일 범위는 실행 전/통합 전에 검사하며 OS 수준 파일 격리는 아니다. Codex의 네트워크 설정과 직접 실행되는 신뢰된 검증 명령의 권한을 혼동하지 않는다. 최종 검증은 통합 소스의 격리 복사본에서 실행한다. 결과 제출 후 실행을 종료하고 사용자 확인을 별도 기록한다.
