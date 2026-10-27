# Tack

개인용 로컬 작업·세션 관리자. Python 3.10 이상 표준 라이브러리만 사용한다. CLI는 HTTP 서버 없이 동작하며 Dashboard는 같은 저장 로직과 SQLite를 사용한다. 새 작업 DB는 DELETE 저널을 사용해 서버 종료·백업 뒤에도 읽기 전용 환경에서 보조 파일 생성 없이 조회한다. 동시 쓰기는 트랜잭션·버전 검사·busy timeout으로 보호한다. Git 저장소 생성·원격 요청·제품 API 호출·알림 발송은 하지 않는다.

## 한 번에 설치

Node.js 18 이상/npm과 Python 3.11 이상이 있는 macOS/Linux/WSL에서 사용한다. npm 런처는 기존 Python 코어를 실행하며 별도 라이브러리를 다운로드하지 않는다. Windows native 설치는 아직 지원하지 않는다.

제공한 저장소에 소스가 게시되면 아래 한 명령으로 설치한다. npm에 별도 게시할 필요는 없다. GitHub 설치에는 Git도 필요하다.

```sh
npx --yes github:Linternas/tack setup
```

npm 패키지 메타데이터는 `@linternas/tack`이다. npm에는 아직 게시하지 않았으며 해당 scope 사용 가능 여부와 게시 권한은 별도 확인이 필요하다.

게시 전에는 배포 tarball로 같은 설치를 실행한다.

```sh
npx --yes --package=./linternas-tack-0.1.0.tgz tack setup
```

설치가 수행하는 일:

- npm 캐시와 별개인 `~/.local/lib/tack/releases/`에 실행 코드 보존
- `~/.local/bin/tack` 설치와 현재 셸의 시작 파일에 PATH 추가
- 기본·실제 CODEX_HOME, 존재하는 회사 홈과 Orca 계정 홈의 DB 경로 쓰기 설정 병합
- 전역 AGENTS에 작업 기록 지침 병합, handoff-state 스킬 설치
- 기존 파일 변경 전 백업, 재설치 중복 방지와 기존 DB 보존

다른 `tack` 명령이나 내용이 다른 handoff-state 스킬은 자동으로 덮어쓰지 않는다. 잘못된 TOML, 자동 병합할 수 없는 표현, named permission profile은 기존 정책을 보호하기 위해 중단하고 원인을 출력한다. setup이 자동 처리하는 권한 설정은 legacy sandbox_workspace_write 방식이다. 설치 성공이 이미 실행 중인 에이전트의 권한 갱신을 뜻하지 않는다. 해당 세션의 적용 방법은 아래 샌드박스 안내를 따른다. setup의 데이터 폴더 쓰기 검사는 일반 설치 프로세스 기준이며 에이전트 sandbox 검증과 구분한다.

설치 후 새 터미널에서 프로젝트 폴더로 이동해 사용한다. `tack task add`가 프로젝트를 자동 등록하므로 별도 init은 필요 없다.

```sh
tack task add '구현할 작업'
tack status
tack dashboard  # 필요할 때만
```

현재 셸에 PATH가 아직 적용되지 않았다면 `~/.local/bin/tack`로 바로 실행한다. Node/npm은 배포 런처에만 필요하며 설치 후 tack 실행은 Python을 사용한다. `AGENT_WORK_HOME`을 지정하면 해당 환경변수를 이후 실행에서도 유지해야 한다. 설치는 DB를 이전·동기화하거나 원격 게시하지 않는다.

호환성을 위해 기존 `work` 명령은 같은 DB를 사용하는 별칭으로 유지한다. 이미 다른 도구가 제공하는 `work`는 덮어쓰지 않는다. 기존 기록을 보존하기 위해 데이터 경로·프로젝트 마커·환경변수 이름(`AGENT_WORK_HOME`)은 그대로 유지한다. Python 내부 모듈명 `agent_work`도 호환용이다.

기존 clone 방식도 유지한다: `python3 install.py`. 이 경로는 CLI와 호환 별칭만 설치하며 자동 초기 설정을 수행하지 않는다. 실행 파일 기본값은 `~/.local/bin/tack`, 데이터 기본값은 `~/.local/share/agent-work`다.

`tack task add`는 프로젝트가 없으면 자동 등록한다. 단순 질문·짧은 설명은 등록하지 않는다. 후속 행동이 있는 분석·리뷰와 코드 변경을 작업으로 관리한다. `tack status`, `task show`, `sessions`, `resume`, `projects`는 읽기 전용으로 동작하며 등록부·DB를 갱신하지 않는다. `tack status`는 미완료 항목과 재개 세션의 요약, `tack status --all`은 완료·보관 항목도 보여준다. 전체 내용은 `task show` 또는 `resume`으로 읽는다.

## 프로젝트와 저장 위치

`tack projects`로 전역 등록 목록을 확인한다. 프로젝트에는 UUID를 부여한다. 같은 Git common directory를 가진 worktree는 같은 프로젝트를 공유하고, 세션에는 실제 checkout·브랜치를 별도로 기록한다.

```sh
tack --workspace /path/to/project init
tack --workspace /new/path init --link EXISTING_PROJECT_UUID
tack --project EXISTING_PROJECT_UUID status
```

폴더 이동·추가 clone은 명시적으로 기존 UUID에 연결할 수 있다. 동일 remote URL만으로 프로젝트를 합치지 않는다.

전역 저장을 쓸 수 없는 환경에서는 **처음 등록하기 전에** `tack init --local`을 사용한다. `.agent-work/project.json`과 같은 폴더의 `work.sqlite3`가 정본이 되며 폴더 내부 `.gitignore`로 제외한다. 전역에 이미 등록한 경로에 별도 로컬 DB를 몰래 만들지 않는다. 전역 저장 접근 실패 시 오류를 보고하며 권한이나 저장 위치를 명시적으로 해결해야 한다. 기존 전역 프로젝트를 로컬로 전환하는 자동 복제·병합은 제공하지 않는다.

`AGENT_WORK_HOME`으로 사용자 데이터 위치를 지정할 수 있다. 이 값을 바꾸면 별도 등록부를 사용하므로 운영 중 임의로 변경하지 않는다.

## 에이전트 샌드박스 권한

전역 설치와 파일 소유권만으로 에이전트의 DB 쓰기가 허용되지는 않는다. SQLite는 DB와 같은 폴더에 저널을 만들어야 하므로 DB 파일만이 아니라 데이터 폴더를 쓰기 허용해야 한다. Codex의 기존 workspace-write 설정은 사용자 config.toml에 아래 항목을 기존 목록과 병합한다. 홈 전체를 허용하거나 DB를 chmod 777로 바꾸지 않는다.

```toml
[sandbox_workspace_write]
writable_roots = ["/home/your-user/.local/share/agent-work"]
```

Codex 설정 위치는 기본 `~/.codex/config.toml` 또는 실제 `CODEX_HOME/config.toml`이다. Orca의 계정별 홈과 별도 회사 계정 홈은 서로 다른 설정을 사용할 수 있으므로 기본 홈 하나만 변경하고 전체 에이전트에 적용됐다고 판단하지 않는다. 실행 중인 에이전트는 시작 시 권한을 유지할 수 있다. 기존 작업을 보존해야 한다면 유휴 상태 확인 후 해당 에이전트만 정상 종료하고, 같은 CODEX_HOME과 같은 세션 ID를 `codex --add-dir /실제/데이터/경로 resume SESSION_ID`로 재개한다. 새 hook 신뢰는 별도이며 기록 권한 문제를 해결하기 위해 일괄 신뢰하지 않는다. 검증은 실제 오류가 났던 에이전트에서 기본 권한으로 작업 생성·checkpoint 저장·별도 조회까지 수행한다.

다른 머신에서는 실제 데이터 절대 경로를 사용한다. `npx … setup`은 지원하는 계정 설정에 데이터 경로를 병합한다. `python3 install.py`는 설정을 바꾸지 않으므로 별도 설정이 필요하다. `AGENT_WORK_HOME`을 지정했다면 해당 실제 경로를 허용한다. named permission profile을 사용하는 환경은 기존 profile의 filesystem에 같은 데이터 경로의 write만 추가하며, 서로 다른 권한 설정 체계를 섞지 않는다.

설정 파일 변경은 이미 실행 중인 턴의 권한을 소급 변경하지 않는다. 실행 중 Codex app-server는 공식 thread 설정 API와 thread/resume의 runtimeWorkspaceRoots로 같은 대화의 다음 턴 권한을 갱신할 수 있다. 앱 재시작이나 새 대화 생성이 반드시 필요한 것은 아니다. 앱/관리자가 실행 시 별도 권한을 주입하는 환경은 그 설정의 쓰기 허용 경로에도 데이터 폴더가 포함돼야 한다. CLI 직접 실행은 `codex --add-dir /실제/데이터/경로`로 명시할 수 있다. 실제 에이전트 환경에서 작업 생성·수정·checkpoint를 저장하고 새 호출로 조회해야 기록 검증 완료다. 읽기 조회 성공이나 샌드박스 밖 실행 성공만으로 판단하지 않는다.

`attempt to write a readonly database`가 나오면 파일 mode뿐 아니라 현재 세션의 writable roots를 확인한다. 현재 세션에서는 환경이 제공하는 승인 절차를 통한 해당 tack 명령 실행만 사용하며, 다른 DB·Markdown·서버 우회 저장소를 만들지 않는다. 권한 승인이 거부되면 미기록 내용과 사유를 보고한다.

## 수정과 체크포인트

수정은 JSON 파일 또는 표준 입력으로 전달한다. 기존 항목·세션 수정에는 조회한 최신 `version`을 넣는다. 충돌은 종료 코드 1 / 상태 409로 반환되며 입력 파일을 보존하고 최신 내용을 확인한다. 자동으로 최신 버전에 덮어쓰지 않는다.

```sh
tack task update LOCAL-0001 --json /tmp/task-update.json
tack checkpoint LOCAL-0001 --json /tmp/checkpoint.json
tack checkpoint LOCAL-0001 --session RUN_SESSION_ID --json /tmp/checkpoint-update.json
tack resume LOCAL-0001
tack sessions
tack sessions --all
```

작업 수정 예시:

```json
{"version":2,"status":"done","resolution":"요청 범위 구현과 관련 검증 완료","note":"완료 근거 기록"}
```

새 체크포인트 예시:

```json
{"goal":"요청한 기능 구현","decisions":"기존 패턴 사용","changed_files":"src/example.py","verification":"단위 테스트 통과, 수동 QA 미실행","blockers":"","next_action":"남은 통합 검증","status":"active"}
```

기존 체크포인트 수정 시 `version`도 전달한다. 세션 상태는 `active`, `blocked`, `completed`다. 세션 종료는 작업 완료·QA 통과를 의미하지 않는다. 작업 완료 상태와 세션 종료를 각각 기록한다. 완료 세션은 기본 재개 목록에서 제외한다. 세션은 여러 개 연결 가능하며 task ID·workspace·branch·agent·thread ID와 전체 체크포인트 이력을 보존한다. CLI는 호출된 checkout과 현재 브랜치를 기본 기록한다. 비밀·인증 정보·대화 전문은 저장하지 않는다.

## Dashboard와 API

`tack dashboard --port 8788`은 loopback에서만 수신한다. `/`는 작업 보드, `/qa`는 QA 워크플로우, `/sessions`는 재개·체크포인트 화면, `/projects`는 전체 프로젝트 요약이다. 프로젝트 선택으로 다른 등록 프로젝트를 열 수 있다. 서버를 끄더라도 CLI는 계속 사용할 수 있다.

- `GET /api/context`: 현재 프로젝트와 프로젝트 목록
- `GET /api/projects`: 전체 프로젝트의 미완료·완료 작업 수와 재개 세션 수
- `GET/POST /api/tasks`, `GET/PATCH /api/tasks/{id}`: 작업·변경 이력
- `GET/POST /api/sessions`, `GET/PATCH /api/sessions/{id}`: 세션·체크포인트
- `GET /api/resume/{task_id}`: 작업·선행 작업·미완료 세션
- `GET /api/export`: 작업·세션 스냅샷. 전체 이력 백업은 SQLite backup 사용

다른 프로젝트 요청은 `?project=UUID`를 포함한다. 수정 JSON은 CLI와 동일하다. 읽기·수정 모두 로컬 Host 검사를 적용하며 교차 사이트 수정은 거절한다.

근거 원문 조회는 프로젝트에 지정한 상대 폴더만 허용한다.

```sh
tack init --source-root docs --source-root src
```

`/api/source?path=docs/spec.md`로 허용된 텍스트 파일을 읽는다. 비밀 파일이 있는 폴더를 허용 목록에 넣지 않는다. 경로 탈출·프로젝트 밖 심볼릭 링크·DB 파일 다운로드는 허용하지 않는다.

## 백업과 이전

`tack backup`은 실행 중에도 SQLite backup API로 일관된 복사본을 만든다. WAL 동작 중인 DB 파일 하나만 복사하지 않는다.

기존 CTMS 보드는 원본 서버를 중지한 뒤 아래 명령으로 이관한다.

```sh
tack migrate --board /path/to/dashboard.sqlite3 --notes /path/to/old-notes --active-note /path/to/current-handoff.md
```

이관은 원본 작업·이력의 전체 행을 대조한 후 전환한다. 원본 DB 백업, 모든 지정 Markdown의 원본과 SHA256, 증거 파일을 프로젝트별 `migration-*` 폴더에 보관하고 `manifest.json`을 생성한다. 선택한 `--active-note`만 재확인 대상 작업·세션으로 연결하며 과거 완료 상태를 변경하지 않는다. 이미 이관했거나 데이터가 있는 대상은 덮어쓰지 않는다. 원본 보존과 별개로 원본 서버를 계속 운영하지 않는다.

과거 기록을 현재 검증으로 취급하지 않는다. 새 도구에 변경이 쌓인 뒤 복구할 때는 새 DB도 먼저 백업하고 변경분을 보존한다. 단순히 옛 DB로 바꾸면 전환 이후 기록이 누락될 수 있다.

## 검증

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
AGENT_WORK_JSDOM_ROOT=/path/to/project/with/jest-environment-jsdom node --test tests/*.cjs
```

런타임에는 외부 의존성이 없다. DOM 테스트에만 기존 프로젝트의 `jest-environment-jsdom`이 필요하다. DOM 테스트는 실제 브라우저·시각 검수가 아니다. 테스트는 임시 DB와 임시 loopback 포트만 사용한다.

## 보관 문서 조회

원본 삭제 전 `tack document import FILE --kind work`로 UTF-8 원문과 SHA256을 DB에 보존할 수 있다. 동일 원문은 중복 생성하지 않고, 내용이 바뀌면 이전 문서를 보존한 채 별도 ID를 부여한다.

```sh
tack document list
tack document show DOC_ID
tack document show archive:DOC_ID
```

CLI 조회는 서버 없이 동작한다. Dashboard의 `/documents`는 보관 문서 목록, `/documents/DOC_ID`는 원문과 참조 링크를 제공한다. 작업의 근거 경로는 `archive:DOC_ID`로 지정한다. 삭제된 원래 경로도 등록된 보관 문서에 한해 조회 가능하며 임의의 외부 파일 읽기는 허용하지 않는다. 보관 문서가 있다는 이유로 과거 작업·QA를 현재 완료나 미해결로 바꾸지 않는다.
