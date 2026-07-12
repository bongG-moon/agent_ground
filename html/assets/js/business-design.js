(() => {
  const details = {
    schedule: {
      status: "새 단계",
      title: "정해진 시간에 Agent 시작",
      why: "담당자가 매일 같은 시간에 파일을 준비하는 반복 작업과 누락 위험을 줄이기 위해 시작 조건을 자동화합니다.",
      how: "스케줄 또는 외부 호출이 업무 요청을 생성하고, 실패 시 자동 재시도보다 담당자에게 실패 상태를 알리는 방식으로 시작합니다.",
      assets: "Agent Builder 실행 API 또는 운영 환경의 스케줄 기능. 승인 전에는 실제 자동 실행을 확정하지 않습니다.",
      check: "지정 시간에 한 번만 실행되고 request_id와 기준 시간이 결과에 남는지 확인합니다.",
      human: "시작 자체는 자동화할 수 있지만 운영 중지와 재실행 권한은 담당자에게 둡니다."
    },
    "data-collection": {
      status: "자동화·변경",
      title: "여러 데이터 소스 자동 조회",
      why: "Excel 파일을 시스템별로 내려받고 합치는 시간이 길고 같은 기준을 반복 적용하기 어렵습니다.",
      how: "질문과 Source Catalog를 data_request로 정규화하고 Oracle, H-API, Datalake, Goodocs 결과를 공통 data_json으로 병합합니다.",
      assets: "reusable_data_flow, Data Request Normalizer, Source Data Components, Data Result Merger, Data Output Builder",
      check: "소스별 row 수와 병합 결과가 일치하고 실패한 소스가 source_results에 구분되는지 확인합니다.",
      human: "자격증명과 조회 권한은 운영자가 관리하며 LLM에 SQL 비밀번호나 Token을 전달하지 않습니다."
    },
    "anomaly-branch": {
      status: "분기 변경",
      title: "이상 여부에 따른 실제 분기",
      why: "모든 설비를 같은 깊이로 분석하면 시간이 낭비되고 정상 건과 이상 건의 후속 작업이 섞입니다.",
      how: "검증된 임계치와 조건으로 정상·이상 edge를 선택합니다. 각 edge에는 조건 라벨과 다음 node ID를 명시합니다.",
      assets: "조건 분기 Component 또는 검증된 Structured Output + Route Gate 패턴",
      check: "경계값 바로 아래·동일·초과 입력으로 각각 기대한 branch가 선택되는지 확인합니다.",
      human: "임계치 변경은 업무 담당자 승인을 받고 변경 이력을 남깁니다."
    },
    "normal-log": {
      status: "새 단계",
      title: "정상 결과 자동 기록",
      why: "이상이 없는 날도 확인했다는 증거가 남아야 하지만 별도 보고서를 수기로 만들 필요는 없습니다.",
      how: "정상 branch에서는 핵심 지표와 실행 시각만 간단히 기록하고 상세 분석 Flow는 실행하지 않습니다.",
      assets: "Data Output Builder 결과와 실행 이력 저장 Adapter",
      check: "정상 조건에서 이상 분석 노드가 실행되지 않고 요약 기록만 생성되는지 확인합니다.",
      human: "장기 보존 기간과 조회 권한은 운영 정책으로 결정합니다."
    },
    "history-merge": {
      status: "자동화·변경",
      title: "작업·정비 이력 자동 병합",
      why: "이상 설비를 찾은 뒤 다시 여러 시스템을 열어 이력을 찾는 과정이 원인 분석을 지연시킵니다.",
      how: "이상 branch의 설비·기간 조건을 후속 data_request로 만들고 작업·정비 이력을 같은 설비와 시간 기준으로 연결합니다.",
      assets: "reusable_data_flow의 multi request, Source Catalog, Data Result Merger",
      check: "이상 설비 ID와 조회 기간이 모든 후속 소스에 동일하게 적용되는지 확인합니다.",
      human: "정비 이력 중 민감 정보가 결과 HTML에 과도하게 노출되지 않도록 필드를 제한합니다."
    },
    "cause-analysis": {
      status: "새 단계",
      title: "원인 후보와 근거 생성",
      why: "담당자가 매번 수기로 원인을 정리하면 사람마다 기준이 달라지고 근거가 빠질 수 있습니다.",
      how: "조회된 사실과 추정 원인을 분리하고 각 원인 후보에 근거 row와 신뢰 수준을 연결합니다.",
      assets: "분석 Prompt Template, 결과 Normalizer, approved 분석 Component",
      check: "데이터에 없는 원인이 확정 표현으로 나오지 않고 모든 후보가 근거를 참조하는지 확인합니다.",
      human: "원인 후보는 판단 보조이며 최종 원인 확정과 조치는 담당자가 수행합니다."
    },
    "html-report": {
      status: "자동화·변경",
      title: "회의용 HTML 리포트 생성",
      why: "수기 표와 메일 본문은 비교가 어렵고 핵심 이상 지점을 빠르게 찾기 어렵습니다.",
      how: "데이터 구조를 분석해 KPI, 추이, 예외 표와 근거를 report_plan으로 구성하고 검증된 Renderer가 HTML을 생성합니다.",
      assets: "html_report_flow, Data Profile Builder, Plan Normalizer, HTML Template Renderer",
      check: "실제 컬럼만 사용하고 이상 설비, 근거와 데이터 범위가 첫 화면에서 확인되는지 점검합니다.",
      human: "외부 공유 전 담당자가 내용과 민감 정보 포함 여부를 확인합니다."
    },
    "human-review": {
      status: "사람 검토",
      title: "담당자 확인과 팀장 승인",
      why: "원인 판단과 대외·조직 공유는 자동화 오류의 영향이 크므로 사람이 책임 있게 확인해야 합니다.",
      how: "결과 확인, 수정, 승인 또는 반려 상태를 명시하고 승인된 경우에만 다음 공유 단계로 이동합니다.",
      assets: "Human Review Gate 설계 패턴과 승인 상태 payload",
      check: "미승인·반려 상태에서는 메일 공유 단계가 실행되지 않는지 확인합니다.",
      human: "이 단계 자체가 핵심 통제 지점이며 자동 통과를 허용하지 않습니다."
    },
    "email-draft": {
      status: "자동화·변경",
      title: "승인된 메일 초안 공유",
      why: "반복되는 보고 형식은 자동화할 수 있지만 잘못된 자동 발송은 피해야 합니다.",
      how: "승인된 요약과 리포트 링크로 메일 초안까지만 만들고 사용자가 수신자와 내용을 확인한 뒤 발송합니다.",
      assets: "Message Formatter 또는 메일 초안 Tool. 자동 Send는 기본 비활성화",
      check: "승인 정보와 리포트 링크가 맞고 자동 발송이 일어나지 않는지 확인합니다.",
      human: "수신자, 민감 정보와 최종 발송은 반드시 사람이 확인합니다."
    }
  };

  const panel = document.getElementById("improvementPanel");
  const closeButton = document.getElementById("closeImprovement");
  let lastTrigger = null;

  const fields = {
    status: document.getElementById("improvementStatus"),
    title: document.getElementById("improvementTitle"),
    why: document.getElementById("improvementWhy"),
    how: document.getElementById("improvementHow"),
    assets: document.getElementById("improvementAssets"),
    check: document.getElementById("improvementCheck"),
    human: document.getElementById("improvementHuman")
  };

  document.querySelectorAll("[data-improvement]").forEach((button) => {
    button.addEventListener("click", () => {
      const detail = details[button.dataset.improvement];
      if (!detail || !panel) return;
      lastTrigger = button;
      Object.entries(fields).forEach(([key, element]) => { if (element) element.textContent = detail[key]; });
      panel.hidden = false;
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      closeButton?.focus();
    });
  });

  closeButton?.addEventListener("click", () => {
    panel.hidden = true;
    lastTrigger?.focus();
  });
})();
