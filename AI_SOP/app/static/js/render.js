export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function field(labelText, value, {tag = "input", className = "editor-field", type, placeholder, rows = 3, dataField} = {}) {
  const wrap = el("label", undefined, className);
  wrap.append(el("span", labelText));
  const input = document.createElement(tag);
  if (type) input.type = type;
  if (placeholder) input.placeholder = placeholder;
  if (rows && tag === "textarea") input.rows = rows;
  if (dataField) input.dataset.field = dataField;
  input.value = value ?? "";
  wrap.append(input);
  return wrap;
}

function lines(values) {
  return (values || []).join("\n");
}

export function renderQuestions(container, questions) {
  clear(container);
  questions.forEach((question, index) => {
    const item = el("div", undefined, "question-item");
    const label = el("label", `${index + 1}. ${question}`);
    label.htmlFor = `answer-${index}`;
    const input = el("textarea");
    input.id = `answer-${index}`;
    input.name = "answer";
    input.rows = 3;
    input.required = true;
    input.placeholder = "실제 업무 기준을 편하게 적어주세요.";
    item.append(label, input);
    container.append(item);
  });
}

function section(root, title, values, emptyLabel = "보완 필요") {
  root.append(el("h3", title));
  const list = el("ul");
  (values?.length ? values : [emptyLabel]).forEach(value => list.append(el("li", value)));
  root.append(list);
}

export function renderSop(container, draft, {editable = true} = {}) {
  clear(container);
  const ir = draft?.ir;
  if (!ir) {
    container.append(el("p", "아직 읽을 수 있는 SOP 결과가 없습니다.", "empty-state"));
    return;
  }
  const root = el("article", undefined, "sop-readable");
  const header = el("div", undefined, "sop-readable-header");
  const heading = el("div");
  heading.append(el("span", "STRUCTURED SOP", "sop-kicker"), el("h2", ir.title), el("p", ir.description));
  header.append(heading);
  if (editable) {
    const edit = el("button", "읽기 화면 수정", "button ghost compact");
    edit.type = "button";
    edit.dataset.action = "edit-sop";
    header.append(edit);
  }
  root.append(header);
  root.append(el("h3", "목적"), el("p", ir.purpose));
  section(root, "입력", ir.inputs);
  root.append(el("h3", "절차"));
  const steps = el("ol", undefined, "sop-step-list");
  ir.steps.forEach(step => {
    const item = el("li", undefined, "sop-step");
    const badge = el("span", String(step.number).padStart(2, "0"), "sop-step-number");
    const body = el("div");
    const title = el("strong", step.title);
    const description = el("p", step.description);
    const meta = el("small", `${step.actor}${step.system ? ` · ${step.system}` : ""}${step.isDecision ? " · 판단 단계" : ""}`);
    body.append(title, description, meta);
    item.append(badge, body);
    steps.append(item);
  });
  root.append(steps);
  section(root, "판단 기준", ir.decisionCriteria);
  section(root, "예외 상황", ir.exceptions);
  section(root, "완료 조건", ir.completionConditions);
  section(root, "보완 필요", ir.openQuestions, "없음");
  section(root, "자동화 후보", ir.automationCandidates, "없음");
  container.append(root);
}

export function renderSopEditor(container, draft) {
  clear(container);
  const ir = draft.ir;
  const root = el("form", undefined, "sop-editor");
  root.addEventListener("submit", event => event.preventDefault());
  const intro = el("div", undefined, "editor-intro");
  intro.append(el("span", "EDIT REVIEWED CONTENT", "sop-kicker"), el("h2", "읽기 화면에서 바로 고치기"), el("p", "수정 반영을 누르면 같은 내용을 기준으로 OKF Markdown과 Mermaid 원문, 흐름도가 함께 갱신됩니다."));
  root.append(intro);

  const overview = el("div", undefined, "editor-grid");
  overview.append(field("문서 제목", ir.title, {dataField: "title"}));
  overview.append(field("한 줄 설명", ir.description, {tag: "textarea", rows: 3, dataField: "description"}));
  overview.append(field("목적", ir.purpose, {tag: "textarea", rows: 4, dataField: "purpose"}));
  root.append(overview);

  const lists = el("div", undefined, "editor-grid editor-lists");
  lists.append(field("입력 자료 (한 줄에 하나)", lines(ir.inputs), {tag: "textarea", rows: 4, dataField: "inputs"}));
  lists.append(field("판단 기준 (한 줄에 하나)", lines(ir.decisionCriteria), {tag: "textarea", rows: 4, dataField: "decisionCriteria"}));
  lists.append(field("예외 상황 (한 줄에 하나)", lines(ir.exceptions), {tag: "textarea", rows: 4, dataField: "exceptions"}));
  lists.append(field("완료 조건 (한 줄에 하나)", lines(ir.completionConditions), {tag: "textarea", rows: 4, dataField: "completionConditions"}));
  lists.append(field("보완 필요 (한 줄에 하나)", lines(ir.openQuestions), {tag: "textarea", rows: 4, dataField: "openQuestions"}));
  lists.append(field("자동화 후보 (한 줄에 하나)", lines(ir.automationCandidates), {tag: "textarea", rows: 4, dataField: "automationCandidates"}));
  root.append(lists);

  const stepsHeading = el("div", undefined, "editor-section-heading");
  stepsHeading.append(el("h3", "절차와 판단 분기"), el("p", "단계 설명을 고치면 읽기 화면과 흐름도에 동시에 반영됩니다."));
  root.append(stepsHeading);
  const stepList = el("div", undefined, "step-editor-list");
  ir.steps.forEach((step, index) => {
    const row = el("fieldset", undefined, "step-editor-row");
    row.dataset.index = String(index);
    const legend = el("legend", `단계 ${index + 1}${step.isDecision ? " · 판단" : ""}`);
    row.append(legend);
    const grid = el("div", undefined, "editor-grid");
    grid.append(field("단계명", step.title, {dataField: "title"}));
    grid.append(field("설명", step.description, {tag: "textarea", rows: 3, dataField: "description"}));
    grid.append(field("담당", step.actor, {dataField: "actor"}));
    grid.append(field("시스템", step.system || "", {dataField: "system"}));
    const decision = el("label", undefined, "editor-check");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(step.isDecision);
    checkbox.dataset.field = "isDecision";
    decision.append(checkbox, el("span", "판단 단계로 표시"));
    grid.append(decision);
    grid.append(field("예(Yes) 다음 단계 또는 설명", step.yesTarget || "", {dataField: "yesTarget"}));
    grid.append(field("아니오(No) 다음 단계 또는 설명", step.noTarget || "", {dataField: "noTarget"}));
    row.append(grid);
    stepList.append(row);
  });
  root.append(stepList);

  const actions = el("div", undefined, "editor-actions");
  const cancel = el("button", "수정 취소", "button ghost");
  cancel.type = "button";
  cancel.dataset.action = "cancel-sop-edit";
  const apply = el("button", "수정 반영", "button primary");
  apply.type = "button";
  apply.dataset.action = "apply-sop-edit";
  actions.append(cancel, apply);
  root.append(actions);
  container.append(root);
}

export function collectSopEditor(container, originalIr) {
  const value = dataField => container.querySelector(`[data-field="${dataField}"]`)?.value.trim() || "";
  const list = dataField => value(dataField).split("\n").map(item => item.trim()).filter(Boolean);
  const steps = [...container.querySelectorAll(".step-editor-row")].map((row, index) => {
    const read = dataField => row.querySelector(`[data-field="${dataField}"]`);
    return {
      number: index + 1,
      title: read("title")?.value.trim() || "",
      description: read("description")?.value.trim() || "",
      actor: read("actor")?.value.trim() || "담당자",
      system: read("system")?.value.trim() || null,
      isDecision: Boolean(read("isDecision")?.checked),
      yesTarget: read("yesTarget")?.value.trim() || null,
      noTarget: read("noTarget")?.value.trim() || null,
      sourceRefs: originalIr.steps[index]?.sourceRefs || [],
    };
  });
  return {
    title: value("title"),
    description: value("description"),
    purpose: value("purpose"),
    inputs: list("inputs"),
    steps,
    decisionCriteria: list("decisionCriteria"),
    exceptions: list("exceptions"),
    completionConditions: list("completionConditions"),
    openQuestions: list("openQuestions"),
    automationCandidates: list("automationCandidates"),
  };
}

export function renderDocuments(container, items, kind, onSelect) {
  clear(container);
  if (!items.length) {
    container.append(el("div", kind === "wiki" ? "아직 승인된 Wiki 문서가 없습니다." : "아직 작성한 초안이 없습니다.", "empty-state"));
    return;
  }
  items.forEach(item => {
    const card = el("article", undefined, "doc-card");
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    const id = kind === "wiki" ? item.documentId : item.draftId;
    card.setAttribute("aria-label", `${item.title || "SOP"} 상세 보기`);
    card.dataset.documentId = id;
    const date = item.publishedAt || item.updatedAt;
    const dateText = date ? new Date(date).toLocaleDateString("ko-KR") : "";
    const meta = kind === "wiki" ? `${item.targetVisibility || "PUBLIC"} · ${dateText}` : `${item.status || "DRAFT"} · ${dateText}`;
    card.append(el("span", meta, "doc-meta"), el("h2", item.title), el("p", item.description || item.ir?.description || "작성 중인 SOP 초안"));
    const footer = el("span", "클릭하여 결과 보기 →", "doc-card-action");
    card.append(footer);
    const open = () => onSelect?.(id, kind, item);
    card.addEventListener("click", open);
    card.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
    });
    container.append(card);
  });
}
