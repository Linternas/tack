<div align="center">

# Tack

**개발 작업과 에이전트 세션을 이어주는 로컬 작업 기록**

CLI · 웹 대시보드 · SQLite

[빠른 시작](#빠른-시작) · [작업 보드](#작업-보드) · [실행 이력](#실행-이력) · [프로젝트와 데이터](#프로젝트와-데이터) · [업데이트](#업데이트)

</div>

Tack은 티켓, 결정, 검증 근거, 세션 체크포인트를 프로젝트별 SQLite에 저장합니다. 다른 세션에서도 같은 티켓을 열어 완료 기준, 선행 작업, 다음 할 일을 확인할 수 있습니다. 에이전트는 평소 쓰는 터미널이나 앱에서 시작하고, Tack에서 실제 작업과 세션을 연결합니다.

## 빠른 시작

macOS, Linux 또는 WSL에서 Node.js 18+, Python 3.11+, Git이 필요합니다. 설치 후 CLI와 대시보드는 Python 표준 라이브러리로 실행됩니다.

```sh
npx --yes github:Linternas/tack setup

cd /path/to/project
tack init
tack task add '로그인 오류 수정' --queue issue --page /login
tack status
tack dashboard --port 8788
```

대시보드는 로컬 주소에서 열립니다. CLI 기록·조회는 서버를 종료한 뒤에도 사용할 수 있습니다. [Tack 대시보드 열기](http://127.0.0.1:8788)

### 작업을 다음 세션으로 이어가기

```sh
tack search '로그인 오류'
tack resume TASK_ID
tack bind ACTUAL_THREAD_ID TASK_ID
tack record add TASK_ID --kind verification --text '로그인 오류 재현 및 관련 테스트 통과'
tack checkpoint TASK_ID --json /tmp/checkpoint.json
```

체크포인트에는 다음 세션에 필요한 현재 목표, 결정, 변경 파일, 검증 결과, 다음 행동을 남깁니다. 진행 중인 세션의 체크포인트를 갱신할 때는 `--session RUN_ID`와 기존 세션의 최신 `version`을 사용합니다.

```json
{
  "goal": "로그인 오류 수정",
  "decisions": "기존 입력 검증 규칙을 적용",
  "changed_files": "src/login.ts",
  "verification": "로그인 단위 테스트 통과",
  "blockers": "",
  "next_action": "실제 화면에서 실패 안내 확인",
  "status": "active"
}
```

기본 검색과 조회는 짧은 요약을 반환합니다. 결정을 내리거나 작업을 시작하기 전에 해당 티켓의 상세 근거를 읽습니다.

```sh
tack record list TASK_ID --kind verification
tack record show RECORD_ID
tack task show TASK_ID --section acceptance --full
tack search '로그인' --all-projects
```

### 에이전트 기록 방식

설치에서 등록한 Codex Hook은 지원되는 환경에서 신뢰 승인과 실제 수신이 확인된 이후 세션 이벤트를 기록합니다. `tack doctor`로 현재 프로젝트의 이벤트 수신을 확인합니다. 기획 판단, 검증 결과, 버전 확인과 작업 완료는 작업 세션이 명시적으로 기록합니다. 세션 종료와 티켓 완료는 각각의 상태로 남깁니다.

## 작업 보드

| 화면 | 할 수 있는 일 |
| --- | --- |
| 작업 보드 | 티켓 목록과 문서 읽기, 상태 필터와 검색, 완료·보관 기록, API 요청 필터, 작업 관계 조회 |
| 노트 | 프로젝트의 아이디어와 참고 기록을 검색하고 편집 |
| 작업 재개·세션 | 티켓과 연결된 체크포인트 및 종료 세션 조회 |
| QA | 검증 절차, 기대·실제 결과와 판정 기록 |
| 실행 이력 | 과거 목표, 역할 실행, 직접 실행한 Codex 세션 기록과 수집된 사용량 조회 |
| 보관 문서 | 이관한 문서 원문 및 참조 링크 조회 |
| 전체 프로젝트 | 등록 프로젝트 조회와 보관·복원, 작업 보드에서 Git 워크트리 확인 |

티켓의 기본 목록은 `todo`, `in_progress`, `issue`, `qa`, `waiting`, `done`, `archived` 중 하나입니다. API 요청 표식은 목록 상태와 별도로 유지됩니다. 완료 처리에는 해결 근거가 필요하고, QA 완료에는 실제 실행 결과와 판정이 필요합니다.

티켓을 선택하면 목록 옆에서 다음 할 일, 상세, 검증 근거와 관련 문서를 읽을 수 있습니다. 좁은 화면에서는 본문이 먼저 열리고 **목록으로** 돌아갈 수 있습니다. 선택한 티켓은 대시보드 URL로 다시 열 수 있습니다.

**설정 → 화면 테마**에서 흑백 **문서** 또는 기존 어두운 **클래식**을 선택할 수 있습니다. 테마 선택은 브라우저에 저장되며 대시보드 전체에 적용됩니다.

```sh
tack status --queue in_progress,todo --limit 10
tack status --api-only --all
tack task add '오류 상태 회귀 검증' --parent PARENT_ID --queue qa --page /login
tack task relations TASK_ID
```

### 근거 문서와 코드 지도

티켓의 **근거 문서·티켓**에 저장소 상대 경로, `archive:DOC_ID` 또는 HTTPS 링크를 넣을 수 있습니다. Markdown은 제목·목록·표·코드 블록을 렌더링합니다. HTML 미리보기는 격리된 프레임에서 문서 자체의 서식을 표시하고 스크립트와 외부 리소스 로드를 제한합니다.

저장소 파일은 등록한 근거 경로 아래에서 조회합니다. `--source-root`를 지정하면 허용 목록을 교체하므로 계속 쓸 경로를 모두 전달합니다.

```sh
tack init --source-root docs --source-root src
tack document import docs/spec.md --kind work
tack document show DOC_ID
```

티켓의 **코드 영향 범위 · 화면 흐름**에서는 코드 항목과 화면 분기를 양방향으로 선택할 수 있습니다. **관계 표와 파일 근거**에는 표시 중인 항목의 파일과 줄 번호가 나옵니다. 저장 기준과 현재 작업본이 달라졌으면 지도에 표시합니다.

```sh
tack map set TASK_ID --json /tmp/task-map.json
tack map show TASK_ID
```

지도 JSON에는 마지막 저장 `version`(첫 저장은 `0`), 기준 Git 커밋의 전체 SHA인 `revision`, 코드 항목 `nodes`, 관계 `edges`, 화면 분기 `flows`를 넣습니다. 분기의 `node_ids`에 확인한 코드 항목 ID를 적으면 화면과 코드가 연결됩니다. 아래 커밋과 파일은 실제 근거로 바꿉니다.

```json
{
  "version": 0,
  "revision": "0000000000000000000000000000000000000000",
  "nodes": [
    {"id": "screen", "kind": "route", "label": "요청 목록", "ref": {"path": "src/requests/page.tsx", "line": 12}},
    {"id": "query", "kind": "hook", "label": "useRequests", "ref": {"path": "src/requests/queries.ts", "line": 26}}
  ],
  "edges": [{"from": "screen", "to": "query", "label": "목록 조회"}],
  "flows": [
    {"step": "목록 진입", "condition": "응답에 행이 없음", "outcome": "빈 상태 표시", "kind": "code", "node_ids": ["screen", "query"], "ref": {"path": "src/requests/page.tsx", "line": 34}}
  ]
}
```

## 실행 이력

예전 버전에서 저장한 목표와 역할·세션 실행은 기존 프로젝트 DB에 남습니다. **실행 이력** 화면에서 저장 당시의 상태와 연결된 티켓, 남겨진 실행 근거를 읽을 수 있습니다. 상태와 시간은 저장된 기록을 기준으로 표시합니다.

수집된 입력·캐시 입력·출력 토큰과 실행 소요 시간은 같은 화면 또는 `tack usage --full`에서 조회합니다. 누락된 사용량은 미수집으로 표시하고, 캐시 입력은 입력 토큰의 일부로 집계합니다.

## 프로젝트와 데이터

기본 데이터 경로는 `~/.local/share/agent-work`입니다. 전역 설치가 관리하는 런타임은 `~/.local/lib/tack/releases/`에 저장됩니다. 제품 요구사항·API 계약·설계처럼 계속 참조할 문서는 프로젝트 저장소에 둡니다.

같은 프로젝트를 새 폴더에서 이어서 작업할 때 기존 프로젝트 ID로 연결하면 티켓과 세션을 공유합니다. 대시보드를 띄운 폴더가 시작 프로젝트의 근거 경로와 워크트리 목록을 조회하는 기준입니다.

```sh
tack projects
tack --workspace /path/to/new-checkout init --link EXISTING_PROJECT_UUID
tack --workspace /path/to/new-checkout project show
tack --workspace /path/to/new-checkout dashboard --port 8788
```

`tack backup`으로 현재 프로젝트 DB의 SQLite 백업을 만들 수 있습니다. 전체 프로젝트 화면의 **보관**은 기록을 유지한 채 읽기 전용으로 전환합니다. **복원**으로 다시 편집할 수 있습니다. 영구 삭제는 별도 확인 후 선택한 프로젝트의 Tack 기록을 삭제하는 기능입니다.

## 업데이트

```sh
tack update
```

이미 설치한 전역 Tack을 업데이트하고 쓰던 프로젝트 ID와 DB를 이어서 사용합니다. 업데이트 후 대시보드를 다시 열면 새 런타임이 적용됩니다. 이전 버전에서 전환할 때는 새 배포본의 업데이트 명령을 한 번 사용합니다.

```sh
npx --yes github:Linternas/tack update

# 새 코드가 있는 로컬 Tack 저장소에서
node bin/tack.cjs update --source .
```

기존 관리형 프로젝트 실행기는 업데이트에서 백업 후 비활성화됩니다. 실행 중인 작업이 있거나 사용자 지정 실행기가 있는 경로는 업데이트 결과에서 별도로 확인할 수 있습니다. 기존 티켓·세션·목표·실행 기록은 같은 DB에서 계속 조회합니다.

## 개발 검증

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
AGENT_WORK_JSDOM_ROOT=/path/to/project-with-jest-environment-jsdom node --test tests/*.cjs
```

Python 테스트 중 일부는 로컬 루프백 포트를 사용합니다. 브라우저 DOM 테스트에는 `jest-environment-jsdom`이 설치된 경로를 지정합니다.
