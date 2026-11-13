import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { focused, exec, home, theme, run, props } from "herdr";

const workQueues = {todo:"할 일",in_progress:"진행 중",issue:"문제",qa:"QA",waiting:"대기",done:"완료",archived:"보관"};
const labels = { todo:"할 일", in_progress:"진행 중", blocked:"차단", waiting_api:"API 대기", waiting_decision:"기획 결정 대기", needs_recheck:"재확인 필요", done:"완료", archived:"보관" };

export default class TackTasks extends View {
  init(_props, cx) {
    const config = props();
    this.view = config?.view === "team" ? "team" : "tasks";
    this.executionBlock = config?.execution_block || null;
    this.wide = config?.wide === true;
    this.team = null;
    this.teamFolder = null;
    this.teamSpace = null;
    this.teamParent = null;
    this.teamGeneration = 0;
    this.teamLoading = false;
    this.teamBusy = false;
    this.teamError = "";
    this.selectedGoal = null;
    this.createForm = false;
    this.assignForm = false;
    this.employee = "implement";
    this.dependencies = [];
    this.form = {};
    for (const key of ["title","acceptance","verification","instructions","files","answer"]) this.form[key] = InputState.new({value:""});
    this.address = null;
    this.space = null;
    this.data = null;
    this.error = "";
    this.busy = false;
    this.loading = false;
    this.picker = false;
    this.generation = 0;
    this.query = InputState.new({ value:"" });
    this.query.on("submit", (_event, cx) => this.refresh(cx));
    this.handle = cx.focus_handle();
    cx.timer.every(1500, cx => this.poll(cx));
    this.poll(cx);
  }

  target() {
    const current = focused();
    if (current?.space !== this.space) return current?.terminal || current?.agent || null;
    return current?.terminal || current?.agent || this.address;
  }

  poll(cx) {
    if (this.view === "team") {
      if (this.teamSpace !== focused()?.space) {
        this.teamSpace = focused()?.space;
        this.teamParent = focused()?.terminal || this.address;
        this.teamFolder = this.space === focused()?.space && this.target() === this.address ? this.data?.project.workspace || focused()?.cwd : focused()?.cwd;
        this.team = null;
        this.selectedGoal = null;
        this.teamGeneration++;
        this.teamLoading = false;
        this.teamBusy = false;
        this.teamError = "";
        this.teamLoadedAt = 0;
      }
      if (this.teamFolder && !this.teamBusy && !this.teamLoading && Date.now() - (this.teamLoadedAt || 0) > 3000) this.teamCall("status",{},cx);
      return;
    }
    const address = this.target();
    const space = focused()?.space;
    if (address !== this.address || space !== this.space) {
      this.address = address;
      this.space = space;
      this.data = null;
      this.loading = false;
      this.error = "";
      this.picker = false;
      this.query.set_value("");
      this.generation++;
      cx.notify();
      this.refresh(cx);
    } else if (address && !this.busy && !this.loading && (!this.data || Date.now() - (this.loadedAt || 0) > 8000)) {
      this.refresh(cx);
    }
  }

  async call(args) {
    const response = await exec(home()+"/.local/bin/tack", ["herdr", ...args], {timeout:12000});
    if (response.code !== 0) {
      let message = response.stderr || "Tack을 실행할 수 없습니다.";
      try { message = JSON.parse(message).error || message; } catch (_) {}
      throw Error(message);
    }
    return JSON.parse(response.stdout);
  }

  refresh(cx) {
    if (!this.address || this.busy || this.loading) return;
    const address = this.address;
    const generation = ++this.generation;
    this.loading = true;
    cx.spawn(async cx => {
      try {
        const data = await this.call(["inspect", address, "--query", this.query.value()]);
        if (generation !== this.generation) return;
        this.data = data;
        this.error = "";
        this.loadedAt = Date.now();
      } catch (error) {
        if (generation === this.generation) { this.data = null; this.error = error.message; }
      } finally {
        if (generation === this.generation) { this.loading = false; cx.notify(); }
      }
    });
  }

  connect(task, cx) {
    if (this.busy || !this.data) return;
    const data = this.data;
    const address = this.address;
    const generation = ++this.generation;
    const args = [task ? "bind" : "unbind", address];
    if (task) args.push(task.id);
    args.push("--session", data.session.key);
    if (data.task) args.push("--from-task", data.task.id);
    this.busy = true;
    this.error = "";
    cx.notify();
    cx.spawn(async cx => {
      try {
        const result = await this.call(args);
        if (generation === this.generation) {
          this.data = result;
          this.picker = false;
          this.loadedAt = Date.now();
        }
      } catch (error) {
        if (generation === this.generation) this.error = error.message;
      } finally {
        this.busy = false;
        if (generation === this.generation) cx.notify();
      }
    });
  }

  renderTasks(cx) {
    const switching = this.target() !== this.address || focused()?.space !== this.space;
    const data = switching ? null : this.data;
    const t = theme();
    const task = data?.task;
    const button = (id, text, action) => div().id(id).px(10).py(7).rounded(7).bg(t.raised).border_1().border_color(t.border).cursor_pointer().text_size(12).hover(el => el.bg(t.hover)).on_click((_event,cx) => { if (!this.busy) action(cx); }).child(text);
    const section = (title, text) => v_flex().gap(5).py(11).border_b(1).border_color(t.border)
      .child(div().text_size(11).text_color(t.dim).child(title))
      .child(div().text_size(12.5).child(text || "미기록"));
    const picker = !data?.session.managed && (!task || this.picker);
    return v_flex().id("tack-tasks").size_full().min_h(0).bg(t.background).text_color(t.foreground).track_focus(this.handle)
      .child(h_flex().h(44).flex_none().px(14).gap(8).items_center().border_b(1).border_color(t.border)
        .child(div().flex_1().font_semibold().child("Tack 작업"))
        .child(button("tack-refresh",this.loading || switching ? "조회 중" : "새로고침",cx => {this.poll(cx);this.refresh(cx);})))
      .child(v_flex().id("tack-scroll").flex_1().min_h(0).overflow_y_scroll().p(14).gap(9)
        .child(div().text_size(11).text_color(t.dim).child(this.address ? `${this.address} · ${switching ? "전환 중" : data?.session.program || "에이전트"}` : "Office에서 직원을 선택하세요."))
        .when(Boolean(data), el => el.child(div().text_size(11).text_color(t.dim).child(data?.project.name || "")))
        .when(Boolean(this.error), el => el.child(div().p(10).rounded(6).bg(t.raised).text_size(12).text_color(t.ansi1).child(this.error)))
        .when(Boolean(task), el => el
          .child(h_flex().gap(8).items_center().child(div().text_size(11).text_color(t.accent).child(task?.id || "")).child(div().text_size(11).text_color(t.dim).child(workQueues[task?.queue] || task?.queue || "")))
          .child(div().font_semibold().text_size(16).child(task?.title || ""))
          .when(!data?.session.managed, el => el.child(h_flex().gap(7).flex_wrap()
            .child(button("tack-change","작업 변경",cx => {this.picker = !this.picker;cx.notify();}))
            .child(button("tack-unbind","연결 해제",cx => this.connect(null,cx)))))
          .child(section("현재 요약",task?.summary))
          .child(section(task?.truncated_fields?.includes("acceptance") ? "완료 조건 · 미리보기" : "완료 조건",task?.acceptance))
          .child(section("다음 행동",task?.next_action))
          .when(Boolean(task?.dependencies?.length), el => el.child(section("선행 작업",task.dependencies.map(d => `${d.id} · ${workQueues[d.queue] || d.queue}`).join("\n"))))
          .child(section("최근 검증 · 미리보기",task?.verification || task?.qa_actual))
          .when(Boolean(task?.resolution), el => el.child(section("처리 결과",task?.resolution))))
        .when(Boolean(data) && picker, el => el
          .child(div().pt(7).text_size(12).font_semibold().child("연결할 작업"))
          .child(Input.new(this.query).h(34).px(8).rounded(7).border_1().border_color(t.input).bg(t.surface).text_color(t.foreground).text_size(12))
          .child(button("tack-search","ID·제목 검색",cx => this.refresh(cx)))
          .children((data?.tasks || []).map(item => button(`tack-bind-${item.id}`,`${item.id} · ${item.title}`,cx => this.connect(item,cx))))
          .when(!data?.tasks.length, el => el.child(div().text_size(12).text_color(t.dim).child("조건에 맞는 미완료 작업이 없습니다.")))))
      .child(div().flex_none().px(14).py(9).border_t(1).border_color(t.border).text_size(10.5).text_color(t.dim).child(this.busy ? "연결 저장 중…" : "실행 연결은 Herdr 기준 · 작업 완료는 검증 후 기록"));
  }

  teamCall(action,payload,cx) {
    if (!this.teamFolder || this.teamBusy || this.teamLoading) return;
    const generation = ++this.teamGeneration;
    this.teamBusy = action !== "status";
    this.teamLoading = true;
    this.teamError = "";
    const args = ["team","--folder",this.teamFolder,"--action",action,"--payload",JSON.stringify(payload)];
    if (this.selectedGoal) args.push("--goal",this.selectedGoal);
    if (this.teamParent) args.push("--parent",this.teamParent);
    cx.notify();
    cx.spawn(async cx => {
      try {
        const data = await this.call(args);
        if (generation !== this.teamGeneration) return;
        this.team = data;
        this.selectedGoal = data.goal?.id || null;
        this.teamLoadedAt = Date.now();
        if (this.wide && !this.selectedGoal && data.goals?.length) {
          const active = data.goals.find(item => ["running","queued","waiting_answer","awaiting_approval","awaiting_acceptance"].includes(item.status));
          this.selectedGoal = (active || data.goals[0]).id;
          this.teamLoadedAt = 0;
        }
        if (action === "create") this.createForm = false;
        if (action === "assign") this.assignForm = false;
        if (action === "reply") this.form.answer.set_value("");
      } catch (error) {
        if (generation === this.teamGeneration) this.teamError = error.message;
      } finally {
        if (generation === this.teamGeneration) { this.teamLoading = false; this.teamBusy = false; cx.notify(); }
      }
    });
  }

  renderTeam(cx) {
    const t = theme();
    const data = this.team;
    const goal = data?.goal;
    const states = {...labels, ready:"준비", queued:"실행 대기", running:"진행 중", working:"실행 중", awaiting_approval:"승인 대기", waiting_answer:"답변 대기", waiting_user:"사용자 답변 대기", waiting_reply:"동료 답변 대기", waiting_jobs:"후속 결과 대기", awaiting_review:"검토 대기", awaiting_acceptance:"최종 확인 대기", paused:"일시 중지", failed:"실패", conflict:"충돌", waiting_contract:"계약 확정 대기", waiting_conditions:"재개 조건 대기"};
    const button = (id,text,action) => div().id(id).px(10).py(7).rounded(7).bg(t.raised).border_1().border_color(t.border).cursor_pointer().text_size(12).hover(el => el.bg(t.hover)).on_click((_e,cx) => {if (!this.teamBusy && !this.teamLoading) action(cx);}).child(text);
    const field = (key,label) => v_flex().gap(4).child(div().text_size(11).text_color(t.dim).child(label)).child(Input.new(this.form[key]).h(32).px(8).border_1().border_color(t.input).rounded(6).bg(t.surface).text_size(12));
    const act = (action,cx) => this.teamCall(action,{version:goal.version},cx);
    const goals = () => (data?.goals || []).map(item => button("team-goal-"+item.id,`${item.id === this.selectedGoal ? "● " : ""}${item.title} · ${states[item.status] || item.status}`,cx => {
      this.selectedGoal = item.id;this.assignForm = false;this.dependencies = [];this.teamCall("status",{},cx);
    }));
    const newGoal = () => button("team-new","+ 목표 만들기",cx => {this.createForm = !this.createForm;cx.notify();});
    const navigation = () => v_flex().id("team-navigation").w(220).flex_none().min_h(0).overflow_y_scroll().p(12).gap(9).border_r(1).border_color(t.border).bg(t.chrome)
      .child(newGoal()).children(goals())
      .child(div().pt(12).text_size(11).font_semibold().text_color(t.dim).child("직원"))
      .children((data?.roles || []).map(role => h_flex().gap(7).py(5)
        .child(div().flex_1().text_size(12).child(role.name))
        .child(div().text_size(10.5).text_color(t.dim).child((data?.jobs || []).some(job => job.employee === role.id && job.status === "working") ? "실행 중" : "대기"))));
    return v_flex().size_full().min_h(0).bg(t.background).text_color(t.foreground)
      .child(h_flex().px(14).py(10).gap(8).items_center().border_b(1).border_color(t.border)
        .child(v_flex().flex_1().min_w(0).child(div().font_semibold().child(data?.project || "프로젝트 연결"))
          .child(div().text_size(10.5).text_color(t.dim).child(data?.enabled ? `${data.mode} · ${data.runtime}` : this.teamFolder?.split("/").pop() || "직원 선택 후 팀 운영을 여세요.")))
        .child(button("team-refresh",this.teamLoading ? "조회 중" : "새로고침",cx => this.teamCall("status",{},cx))))
      .child(h_flex().flex_1().min_h(0).items_stretch()
        .when(this.wide && Boolean(data?.enabled),el => el.child(navigation()))
        .child(v_flex().id("team-scroll").flex_1().min_w(0).min_h(0).overflow_y_scroll().p(14).gap(10)
        .when(Boolean(this.teamError),el => el.child(div().p(9).rounded(6).bg(t.raised).text_color(t.ansi1).text_size(12).child(this.teamError)))
        .when(data?.enabled === false,el => el.child(div().text_size(12).child("프로젝트 폴더에서 npx --yes --package github:Linternas/tack tack-orchestration setup 경로로 하네스를 설치하세요.")))
        .when(Boolean(data?.enabled) && data.runtime !== "herdr-codex-exec",el => el.child(button("team-connect","이 프로젝트의 실행을 Herdr에 연결",cx => this.teamCall("connect",{},cx))))
        .when(Boolean(data?.enabled),el => el
          .when(!this.wide,el => el.child(newGoal()))
          .when(this.createForm,el => el.child(v_flex().gap(8).p(10).rounded(8).bg(t.surface)
            .child(field("title","개발 목표"))
            .child(field("acceptance","완료 조건"))
            .child(field("verification","최종 검증 명령"))
            .child(button("team-create","목표 등록 · 기획 실행",cx => this.teamCall("create",{title:this.form.title.value(),acceptance:this.form.acceptance.value(),verification_text:this.form.verification.value()},cx)))))
          .when(!this.wide,el => el.children(goals()))
          .when(Boolean(goal),el => el
            .child(div().pt(8).font_semibold().text_size(14).child(goal?.title || ""))
            .child(div().text_size(11.5).text_color(t.muted).child(data?.acceptance || ""))
            .children((data?.verification_results || []).map(check => div().text_size(11).text_color(check.status === "succeeded" ? t.accent : t.ansi1).child(`${check.argv.join(" ")} · ${check.status} · exit ${check.exit_code}`)))
            .child(div().text_size(11).text_color(t.accent).child(`${states[goal?.status] || goal?.status} · 실행 ${data?.scheduler?.running || 0}/${data?.scheduler?.capacity || 0} · 대기 ${data?.scheduler?.queued || 0} · ${goal?.token_used || 0} tokens`))
            .when(Boolean(goal?.error),el => el.child(div().text_size(12).text_color(t.ansi1).child(goal?.error || "")))
            .child(h_flex().gap(6).flex_wrap()
              .when(!goal?.worker && ["ready","paused","failed","needs_recheck","waiting_answer","awaiting_approval"].includes(goal?.status),el => el.child(button("team-run","실행 · 재개",cx => act("run",cx))))
              .when(goal?.status === "awaiting_approval",el => el.child(button("team-approve","범위 승인 · 실행",cx => act("approve",cx))))
              .when(Boolean(goal?.worker) || goal?.status === "queued",el => el.child(button("team-pause","중지 요청",cx => act("pause",cx))))
              .when(goal?.status === "awaiting_acceptance",el => el.child(button("team-accept","최종 완료 승인",cx => act("accept",cx))))
              .when(goal?.status !== "done",el => el.child(button("team-assign-open","+ 업무 배정",cx => {this.assignForm = !this.assignForm;cx.notify();}))))
            .when(this.assignForm,el => el.child(v_flex().p(10).gap(8).rounded(8).bg(t.surface)
              .child(div().text_size(11).child("담당 역할"))
              .child(h_flex().gap(5).flex_wrap().children((data?.roles || []).map(role => button("team-role-"+role.id,(this.employee === role.id ? "● " : "")+role.name,cx => {this.employee = role.id;cx.notify();}))))
              .child(field("instructions","업무 지시 · 완료 조건"))
              .child(field("files","허용 파일 · 쉼표 구분"))
              .child(div().text_size(11).child("선행 업무 선택"))
              .children((data?.jobs || []).map(job => button("team-dep-"+job.id,(this.dependencies.includes(job.id) ? "✓ " : "")+job.id+" · "+job.title,cx => {
                this.dependencies = this.dependencies.includes(job.id) ? this.dependencies.filter(id => id !== job.id) : [...this.dependencies,job.id];cx.notify();
              })))
              .child(button("team-assign","배정 · 승인 대기에 추가",cx => this.teamCall("assign",{version:goal.version,title:this.form.instructions.value().slice(0,180),employee:this.employee,instructions:this.form.instructions.value(),files:this.form.files.value().split(",").map(v => v.trim()).filter(Boolean),depends_on:this.dependencies,client_id:"herdr-"+Date.now()},cx)))))
            .children((data?.jobs || []).map(job => v_flex().id("team-job-"+job.id).p(10).gap(6).rounded(8).bg(t.surface).border_1().border_color(t.border)
              .child(div().text_size(10.5).text_color(t.accent).child(`${job.employee} · ${states[job.status] || job.status} · ${job.id}`))
              .child(div().font_semibold().text_size(12).child(job.title))
              .when(job.status === "awaiting_approval",el => el.child(div().text_size(11.5).child(job.instructions || "")))
              .when(Boolean(job.files?.length),el => el.child(div().text_size(10.5).text_color(t.dim).child("허용 파일 · "+job.files.join(", "))))
              .when(Boolean(job.depends_on?.length),el => el.child(div().text_size(10.5).text_color(t.dim).child("선행 · "+job.depends_on.join(", "))))
              .when(Boolean(job.wait_reason),el => el.child(div().text_size(11).text_color(t.dim).child(job.wait_reason)))
              .when(Boolean(job.terminal),el => el.child(button("team-terminal-"+job.id,"실행 터미널 보기 · "+job.terminal,() => run("terminal.peek",{terminal:job.terminal}))))
              .when(["failed","paused","conflict"].includes(job.status),el => el.child(button("team-retry-"+job.id,"재시도",cx => this.teamCall("retry",{job_id:job.id,version:job.version},cx))))
              .when(Boolean(job.result),el => el.child(div().text_size(11.5).child(job.result)))
              .when(job.status === "waiting_user",el => el
                .child(div().text_size(12).child(job.question))
                .when(Boolean(job.question_decision?.reason),el => el.child(div().text_size(11).text_color(t.dim).child(job.question_decision.reason)))
                .children((job.question_decision?.options || []).map(option => v_flex().gap(4).child(button("team-option-"+job.id+option.id,option.label+(option.id === job.question_decision.recommended_id ? " · 권장" : ""),cx => this.teamCall("reply",{job_id:job.id,version:job.version,body:"",option_id:option.id},cx))).child(div().text_size(11).text_color(t.dim).child(option.description))))
                .child(field("answer","직접 답변"))
                .child(button("team-reply-"+job.id,"답변 · 재개",cx => this.teamCall("reply",{job_id:job.id,version:job.version,body:this.form.answer.value()},cx)))))))))
      )
      .child(div().px(14).py(8).border_t(1).border_color(t.border).text_size(10.5).text_color(t.dim).child(this.teamBusy ? "요청 처리 중…" : "실제 실행·검증 결과를 Tack에 기록합니다."));
  }

  render(cx) {
    const t = theme();
    const tab = (view,label) => div().id("tack-view-"+view).when(this.wide,el => el.w(140)).when(!this.wide,el => el.flex_1()).px(12).py(9).bg(this.view === view ? t.selected : t.chrome).text_color(this.view === view ? t.accent : t.muted).cursor_pointer().font_semibold().text_size(12).on_click((_e,cx) => {
      this.view = view;
      if (view === "team" && !this.teamFolder) this.teamFolder = this.data?.project.workspace || focused()?.cwd;
      this.poll(cx);cx.notify();
    }).child(label);
    return v_flex().size_full().min_h(0)
      .child(h_flex().flex_none().child(tab("team","팀 운영")).child(tab("tasks","세션 작업"))
        .when(this.wide,el => el.child(div().flex_1()))
        .when(Boolean(this.executionBlock),el => el.child(div().id("tack-execution-toggle").px(14).py(9).bg(t.chrome).text_color(t.muted).text_size(12).cursor_pointer().on_click(() => run("layout.toggle",{id:this.executionBlock})).child("실행 터미널"))))
      .child(div().flex_1().min_h(0).child(this.view === "team" ? this.renderTeam(cx) : this.renderTasks(cx)));
  }

}
