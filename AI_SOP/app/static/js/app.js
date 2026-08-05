import { api } from "./api.js?v=20260805-3";
import { clear, collectSopEditor, renderDocuments, renderQuestions, renderSop, renderSopEditor } from "./render.js";
import { renderMermaidDiagram } from "./diagram.js?v=20260805-3";

const state = { draft: null, files: [], inputSignature: null, uploadedFileCount: 0 };
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let toastTimer;

const WORK_EXAMPLES = {
  "반복 보고 업무": "매주 월요일 오전에 지난 1주일간 설비 이상 현황을 확인합니다. 설비 관리 시스템에서 알람 이력과 가동 데이터를 내려받아 전주와 비교합니다. 관리 기준을 초과한 설비가 있으면 담당자에게 원인과 조치 계획을 확인합니다. 확인 결과, 조치 담당자, 완료 예정일을 주간 보고서에 정리하여 팀장에게 공유합니다. 데이터가 누락되면 시스템 담당자에게 재추출을 요청하며, 보고서가 공유되고 담당자가 내용을 확인하면 업무를 완료합니다.",
  "장애 초동 대응": "생산 설비에서 장애 알람이 발생하면 알람 코드와 발생 시각을 먼저 확인합니다. 안전 관련 알람이면 즉시 설비를 정지하고 현장 책임자에게 전화로 알립니다. 일반 장애는 설비 상태와 최근 작업 이력을 확인한 뒤 담당 엔지니어에게 메신저로 증상과 화면 캡처를 전달합니다. 조치 과정과 재가동 시각을 장애 기록에 남기며, 정상 가동이 30분 이상 유지되고 책임자가 확인하면 대응을 종료합니다.",
  "신규자 인수인계": "신규자가 배치되면 첫날에 담당 업무 목록, 사용하는 시스템, 정기 일정과 주요 연락처를 설명합니다. 필요한 시스템 권한을 신청하고 접속 여부를 함께 확인합니다. 첫 주에는 실제 업무 한 건을 시연한 뒤 신규자가 같은 절차를 직접 수행하도록 합니다. 이해하지 못한 단계는 다시 설명하고 인수인계 체크리스트에 결과를 기록합니다. 필수 권한이 모두 발급되고 신규자가 기본 업무를 독립적으로 완료하면 인수인계를 마칩니다."
};

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

function busy(button, active, label) {
  if (!button) return;
  button.disabled = active;
  if (active) {
    button.dataset.label = button.textContent;
    button.textContent = label;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
}

function setProgress(number) {
  $$(".progress-list li").forEach((item, index) => {
    item.classList.toggle("current", index + 1 === number);
    item.classList.toggle("done", index + 1 < number);
  });
}

async function initialize() {
  try {
    const [session, status] = await Promise.all([api.session(), api.status()]);
    $("#user-id").textContent = `사용자 ${session.employeeId}`;
    $("#system-status").textContent = status.mode === "DEMO" ? "체험 모드" : "서버 연결됨";
    $("#template-version").textContent = status.templateAvailable ? status.templateCommit.slice(0, 10) : "구조 검증 모드";
  } catch (error) {
    $("#system-status").textContent = "연결 오류";
    toast(error.message);
  }
  route();
}

function route() {
  const name = location.hash.replace("#", "") || "write";
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `${name}-view`));
  $$(".nav-item").forEach(link => {
    const active = link.dataset.view === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
  if (name === "drafts") loadDrafts();
  if (name === "wiki") loadWiki();
}

function showFiles() {
  const list = $("#file-list");
  clear(list);
  state.files.forEach((file, index) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "제거";
    remove.addEventListener("click", () => { state.files.splice(index, 1); showFiles(); });
    item.append(name, remove);
    list.append(item);
  });
}

function addFiles(files) { state.files.push(...[...files]); showFiles(); }

function currentInputSignature(description) {
  return JSON.stringify({ description, files: state.files.map(file => [file.name, file.size, file.lastModified]) });
}

async function startDraft() {
  const button = $("#start-button");
  const description = $("#description").value.trim();
  if (description.length < 5) { toast("업무 설명을 5자 이상 적어주세요."); $("#description").focus(); return; }
  busy(button, true, "개인 초안 저장 중…");
  const inputSignature = currentInputSignature(description);
  try {
    if (!state.draft || state.inputSignature !== inputSignature) {
      state.draft = await api.createDraft(description);
      state.inputSignature = inputSignature;
      state.uploadedFileCount = 0;
    }
    while (state.uploadedFileCount < state.files.length) {
      await api.upload(state.draft.draftId, state.files[state.uploadedFileCount]);
      state.uploadedFileCount += 1;
    }
    const plan = await api.questions(state.draft.draftId);
    if (plan.questions.length) {
      renderQuestions($("#question-list"), plan.questions);
      $("#interview-panel").classList.remove("hidden");
      setProgress(2);
      $("#interview-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      await generateDraft();
    }
  } catch (error) {
    const retryHint = state.draft && state.inputSignature === inputSignature ? " 개인 초안은 저장됐습니다. 다시 누르면 같은 초안에서 재시도합니다." : "";
    toast(error.message + retryHint);
  } finally {
    busy(button, false);
  }
}

async function submitAnswers(event) {
  event.preventDefault();
  const button = event.submitter;
  busy(button, true, "SOP 구성 중…");
  try {
    const answers = $$("#question-list textarea").map(input => input.value.trim());
    for (const [index, answer] of answers.entries()) if (answer) await api.message(state.draft.draftId, answer, index);
    await generateDraft();
  } catch (error) {
    toast(error.message);
  } finally {
    busy(button, false);
  }
}

async function renderGeneratedArtifacts(draft) {
  state.draft = draft;
  renderSop($("#structured-panel"), state.draft);
  $("#markdown-panel code").textContent = state.draft.markdown;
  $("#mermaid-panel code").textContent = state.draft.mermaid;
  $("#diagram-error").classList.add("hidden");
  $("#diagram-canvas").textContent = "흐름도를 그리는 중입니다…";
  try {
    await renderMermaidDiagram($("#diagram-canvas"), state.draft.mermaid);
  } catch (error) {
    $("#diagram-canvas").replaceChildren();
    $("#diagram-error").textContent = `흐름도를 그리지 못했습니다. Mermaid 원문을 확인해 주세요. (${error.message})`;
    $("#diagram-error").classList.remove("hidden");
  }
  const validation = state.draft.validation;
  $("#validation-summary").textContent = validation?.passed ? "BoI 템플릿 검증을 통과했습니다." : "초안은 생성됐지만 일부 BoI 검증 항목을 확인해야 합니다.";
  $("#review-panel").classList.remove("hidden");
  setProgress(3);
  $("#review-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function generateDraft() { await renderGeneratedArtifacts(await api.generate(state.draft.draftId)); }

function switchTab(button) {
  const owner = button.closest(".stage-panel, .detail-dialog-shell") || document;
  const tablist = button.closest("[role=tablist]");
  (tablist ? tablist.querySelectorAll('[role="tab"]') : $$('[role="tab"]')).forEach(tab => tab.setAttribute("aria-selected", String(tab === button)));
  owner.querySelectorAll(":scope > .tab-panel").forEach(panel => panel.classList.add("hidden"));
  $("#" + button.getAttribute("aria-controls"))?.classList.remove("hidden");
}

async function applyEditedSop() {
  if (!state.draft?.ir) return;
  const button = $("[data-action=apply-sop-edit]");
  const ir = collectSopEditor($("#structured-panel"), state.draft.ir);
  busy(button, true, "전체 산출물 갱신 중…");
  try {
    await renderGeneratedArtifacts(await api.revise(state.draft.draftId, ir));
    toast("수정 내용을 읽기 화면, 흐름도, OKF Markdown, Mermaid 원문에 반영했습니다.");
  } catch (error) {
    toast(error.message);
  } finally {
    busy(button, false);
  }
}

async function openDocumentDetail(id, kind) {
  try {
    const item = kind === "wiki" ? await api.wikiDetail(id) : await api.draft(id);
    $("#detail-eyebrow").textContent = kind === "wiki" ? "APPROVED WIKI" : "PRIVATE DRAFT";
    $("#detail-title").textContent = item.title || "문서 상세";
    $("#detail-description").textContent = item.description || item.ir?.description || "";
    $("#detail-meta").textContent = kind === "wiki" ? `${item.targetVisibility || "PUBLIC"} · ${item.status || "PUBLISHED"}` : `${item.status || "DRAFT"} · ${item.modelId || "모델 정보 없음"}`;
    renderSop($("#detail-structured-panel"), item, {editable: false});
    $("#detail-markdown-panel code").textContent = item.markdown || "";
    $("#detail-mermaid-panel code").textContent = item.mermaid || "";
    $("#detail-diagram-error").classList.add("hidden");
    $("#detail-diagram-canvas").textContent = "흐름도를 그리는 중입니다…";
    try {
      await renderMermaidDiagram($("#detail-diagram-canvas"), item.mermaid || "");
    } catch (error) {
      $("#detail-diagram-canvas").replaceChildren();
      $("#detail-diagram-error").textContent = `흐름도를 그리지 못했습니다. Mermaid 원문을 확인해 주세요. (${error.message})`;
      $("#detail-diagram-error").classList.remove("hidden");
    }
    switchTab($("#detail-tab-structured"));
    $("#document-detail-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function approve(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#approval-dialog").close(); return; }
  const confirmed = $("#confirm-approval").checked;
  if (!confirmed) { $("#approval-error").textContent = "등록 승인 확인란을 체크해 주세요."; return; }
  const sensitiveReviewed = $("#confirm-sensitive").checked;
  if (!sensitiveReviewed) { $("#approval-error").textContent = "민감정보 공개 범위 확인란을 체크해 주세요."; return; }
  const button = $("#approve-button");
  busy(button, true, "등록 중…");
  try {
    const visibility = $('input[name=visibility]:checked').value;
    await api.approve(state.draft.draftId, visibility, true, true);
    $("#approval-dialog").close();
    setProgress(4);
    toast("승인된 문서를 Wiki에 등록했습니다.");
    location.hash = "wiki";
  } catch (error) {
    $("#approval-error").textContent = error.message;
  } finally {
    busy(button, false);
  }
}

async function loadDrafts() {
  try { renderDocuments($("#draft-list"), await api.drafts(), "draft", openDocumentDetail); }
  catch (error) { toast(error.message); }
}

async function loadWiki() {
  try { renderDocuments($("#wiki-list"), await api.wiki(), "wiki", openDocumentDetail); }
  catch (error) { toast(error.message); }
}

window.addEventListener("hashchange", route);
$("#start-button").addEventListener("click", startDraft);
$("#question-form").addEventListener("submit", submitAnswers);
$("#file-input").addEventListener("change", event => addFiles(event.target.files));
const drop = $("#drop-zone");
["dragenter", "dragover"].forEach(name => drop.addEventListener(name, event => { event.preventDefault(); drop.classList.add("dragging"); }));
["dragleave", "drop"].forEach(name => drop.addEventListener(name, event => { event.preventDefault(); drop.classList.remove("dragging"); }));
drop.addEventListener("drop", event => addFiles(event.dataTransfer.files));
$$('[data-example]').forEach(button => button.addEventListener("click", () => {
  const example = WORK_EXAMPLES[button.dataset.example];
  if (!example) return;
  $("#description").value = example;
  $("#description").focus();
  toast(`${button.dataset.example} 예시를 불러왔습니다.`);
}));
$$('[role=tab]').forEach(button => button.addEventListener("click", () => switchTab(button)));
$("#structured-panel").addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "edit-sop") renderSopEditor($("#structured-panel"), state.draft);
  if (action === "cancel-sop-edit") renderSop($("#structured-panel"), state.draft);
  if (action === "apply-sop-edit") applyEditedSop();
});
$("#approve-open").addEventListener("click", () => { $("#approval-error").textContent = ""; $("#confirm-approval").checked = false; $("#confirm-sensitive").checked = false; $("#approval-dialog").showModal(); });
$("#approval-form").addEventListener("submit", approve);
$("#detail-close").addEventListener("click", () => $("#document-detail-dialog").close());
$("#document-detail-dialog").addEventListener("click", event => { if (event.target === event.currentTarget) event.currentTarget.close(); });
initialize();
