from __future__ import annotations

"""06 Deterministic and safe BEFORE/AFTER flow-chart HTML renderer.

The LLM supplies graph JSON only.  Every visible value is HTML-escaped, node
positions and SVG connectors are calculated by one renderer-owned script, and
no model-authored HTML/CSS/JavaScript is executed.
"""

import html
import re
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


STATE_LABELS = {
    "unchanged": "기존과 동일",
    "modified": "변경",
    "added": "신규",
    "human_review": "사람 검토",
    "removed": "제거",
}
TYPE_LABELS = {
    "start": "시작", "process": "처리", "decision": "조건 판단",
    "merge": "분기 합류", "human_review": "사람 검토", "end": "종료",
}


def render_business_design_html(agent_design_value: Any) -> dict[str, Any]:
    payload = _payload(agent_design_value)
    design = _dict(payload.get("agent_design"))
    visual = _dict(design.get("flow_visualization"))
    before = _dict(visual.get("before"))
    after = _dict(visual.get("after"))
    details = [_dict(item) for item in _list(design.get("improvement_details")) if isinstance(item, dict)]

    title = _clean(design.get("report_title")) or "업무 AI Agent 개선 설계"
    body = "".join([
        '<nav class="report-nav" aria-label="결과 화면 이동"><button id="reportBack" type="button">← 이전 화면</button><a href="#improvement-details">개선 설명으로 이동</a></nav>',
        _hero(design, payload),
        _legend(),
        '<section class="comparison" aria-label="BEFORE와 AFTER 업무 Flow 비교">',
        _chart_panel("BEFORE", "현재 업무 흐름", before, "before"),
        _chart_panel("AFTER", "Agent 적용 후 흐름", after, "after"),
        "</section>",
        _detail_section(details),
        _recommendation_section(design.get("recommended_capabilities")),
        _roadmap_section(design.get("implementation_roadmap")),
        _risk_section(design.get("risk_controls")),
        _notes_section(design),
    ])
    document = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; img-src data:; connect-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body>"
        f'<a class="skip-link" href="#main">본문으로 이동</a><main id="main">{body}</main>'
        f'<script id="agent-ground-flow-runtime">{SCRIPT}</script></body></html>'
    )
    security = {
        "passed": _security_passed(document),
        "policy": "escaped_graph_data_and_renderer_owned_script_only",
        "renderer_script_count": document.count('<script id="agent-ground-flow-runtime">'),
        "size_bytes": len(document.encode("utf-8")),
        "llm_html_executed": False,
    }
    return {**payload, "html_result": {"title": title, "html": document, "security_report": security}}


def _hero(design: dict[str, Any], payload: dict[str, Any]) -> str:
    summary = _clean(design.get("executive_summary")) or "업무 흐름과 Agent 적용 후 변화를 비교합니다."
    validation = _dict(payload.get("design_validation"))
    changed = validation.get("changed_nodes", 0)
    return (
        '<header class="hero"><div class="eyebrow">BUSINESS AGENT DESIGN</div>'
        f'<h1>{_e(design.get("report_title") or "업무 AI Agent 개선 설계")}</h1>'
        f'<p>{_e(summary)}</p><div class="hero-meta"><span>변경 node <strong>{_e(changed)}</strong></span>'
        '<span>BEFORE / AFTER 비교</span><span>분기 조건 포함</span></div></header>'
    )


def _legend() -> str:
    items = "".join(
        f'<li><span class="legend-dot state-{state}"></span><strong>{_e(label)}</strong></li>'
        for state, label in STATE_LABELS.items()
    )
    return f'<section class="legend" aria-label="변경 상태 범례"><h2>변경 상태</h2><ul>{items}</ul></section>'


def _chart_panel(kicker: str, title: str, graph: dict[str, Any], theme: str) -> str:
    nodes = [_dict(item) for item in _list(graph.get("nodes")) if isinstance(item, dict)]
    edges = [_dict(item) for item in _list(graph.get("edges")) if isinstance(item, dict)]
    node_html = "".join(_graph_node(node, theme) for node in nodes)
    edge_html = "".join(
        '<span class="edge-meta" '
        f'data-source="{_a(edge.get("source"))}" data-target="{_a(edge.get("target"))}" '
        f'data-label="{_a(edge.get("branch_label"))}" data-condition="{_a(edge.get("condition"))}" '
        f'data-state="{_a(_state(edge.get("change_state")))}"></span>'
        for edge in edges
    )
    accessible = "".join(
        f'<li><strong>{_e(node.get("label"))}</strong> — {_e(node.get("actor"))}, {_e(STATE_LABELS.get(_state(node.get("change_state")), "기존과 동일"))}</li>'
        for node in nodes
    )
    return (
        f'<article class="chart-panel theme-{theme}"><div class="panel-head"><span>{_e(kicker)}</span><h2>{_e(title)}</h2>'
        f'<p>{len(nodes)} nodes · {len(edges)} connections</p></div>'
        f'<div class="flowchart" data-theme="{_a(theme)}"><svg class="connectors" aria-hidden="true"></svg>'
        f'<div class="node-layer">{node_html}</div><div class="edge-layer" hidden>{edge_html}</div></div>'
        f'<details class="accessible-flow"><summary>텍스트 순서로 확인</summary><ol>{accessible}</ol></details></article>'
    )


def _graph_node(node: dict[str, Any], theme: str) -> str:
    node_id = _clean(node.get("node_id"))
    node_type = _node_type(node.get("node_type"))
    state = _state(node.get("change_state"))
    detail_id = _clean(node.get("improvement_detail_id"))
    button = ""
    if theme == "after" and state in {"modified", "added", "human_review"} and detail_id:
        button = (
            f'<button class="detail-button" type="button" data-detail-id="{_a(detail_id)}" '
            'aria-expanded="false">개선 설명</button>'
        )
    assets = _strings(node.get("recommended_asset_ids"))
    asset_tags = "".join(f'<span class="asset-tag">{_e(item)}</span>' for item in assets[:3])
    return (
        f'<article class="flow-node type-{_a(node_type)} state-{_a(state)}" data-node-id="{_a(node_id)}" tabindex="0">'
        '<div class="node-shell">'
        f'<div class="node-top"><span class="type-label">{_e(TYPE_LABELS[node_type])}</span>'
        f'<span class="state-label">{_e(STATE_LABELS[state])}</span></div>'
        f'<h3>{_e(node.get("label"))}</h3><p>{_e(node.get("description"))}</p>'
        f'<div class="actor">담당 · {_e(node.get("actor") or "업무 담당자")}</div>'
        f'<div class="asset-tags">{asset_tags}</div>{button}</div></article>'
    )


def _detail_section(details: list[dict[str, Any]]) -> str:
    if not details:
        return '<section class="details-section"><h2>개선 설명</h2><p class="empty">표시할 개선 설명이 없습니다.</p></section>'
    panels = []
    for index, item in enumerate(details):
        detail_id = _clean(item.get("improvement_detail_id"))
        assets = _strings(item.get("recommended_asset_ids"))
        panels.append(
            f'<article class="detail-panel" id="detail-{_a(detail_id)}" data-detail-panel="{_a(detail_id)}" '
            f'tabindex="-1" {"" if index == 0 else "hidden"}>'
            f'<div class="detail-number">IMPROVEMENT {index + 1:02d}</div><h3>{_e(item.get("title"))}</h3>'
            f'<div class="detail-grid"><div><h4>왜 바꾸나요?</h4><p>{_e(item.get("why_change"))}</p></div>'
            f'<div><h4>어떻게 개선하나요?</h4><p>{_e(item.get("how_to_improve"))}</p></div></div>'
            f'<div class="detail-grid"><div><h4>Flow / Component</h4>{_chips(assets)}</div>'
            f'<div><h4>사람 확인</h4><p>{_e(item.get("human_review"))}</p></div></div>'
            f'<div class="detail-grid"><div><h4>연결 방법</h4>{_list_html(item.get("connection_guidance"))}</div>'
            f'<div><h4>완료 확인</h4>{_list_html(item.get("acceptance_checks"))}</div></div></article>'
        )
    return (
        '<section class="details-section" id="improvement-details"><div class="section-head"><span>CLICK TO EXPLORE</span>'
        '<h2>선택한 단계의 개선 설명</h2><p>AFTER Flow의 파란색·초록색·주황색 node에서 버튼을 누르세요.</p></div>'
        f'<div class="detail-panels">{"".join(panels)}</div></section>'
    )


def _recommendation_section(value: Any) -> str:
    cards = []
    for item in map(_dict, _list(value)):
        if not item:
            continue
        links = "".join(_safe_link(link) for link in _strings(item.get("source_links"))[:3])
        cards.append(
            '<article class="capability-card">'
            f'<div class="capability-id">{_e(item.get("catalog_id"))}</div><h3>{_e(item.get("title"))}</h3>'
            f'<p>{_e(item.get("why_recommended"))}</p>{_chips(item.get("building_blocks"))}<div class="source-links">{links}</div></article>'
        )
    return _section_cards("추천 Flow / Component", "검증된 카탈로그 ID와 추천 이유", cards)


def _roadmap_section(value: Any) -> str:
    cards = []
    for item in map(_dict, _list(value)):
        if item:
            cards.append(f'<article class="roadmap-card"><span>{_e(item.get("phase"))}</span><h3>{_e(item.get("goal"))}</h3>{_list_html(item.get("tasks"))}<p class="success"><strong>완료 기준</strong> {_e(item.get("success_check"))}</p></article>')
    return _section_cards("구현 순서", "작게 검증하고 다음 단계로 확장", cards, "roadmap-grid")


def _risk_section(value: Any) -> str:
    rows = []
    for item in map(_dict, _list(value)):
        if item:
            rows.append(f'<tr><th scope="row">{_e(item.get("risk"))}</th><td>{_e(item.get("control"))}</td><td>{_e(item.get("human_checkpoint"))}</td></tr>')
    if not rows:
        return ""
    return f'<section class="content-section"><div class="section-head"><span>SAFETY</span><h2>위험과 사람 확인 지점</h2></div><div class="table-wrap"><table><thead><tr><th>위험</th><th>통제 방법</th><th>사람 확인</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'


def _notes_section(design: dict[str, Any]) -> str:
    return (
        '<section class="notes-grid"><article><h2>가정</h2>' + _list_html(design.get("assumptions")) + '</article>'
        '<article><h2>추가로 확인할 질문</h2>' + _list_html(design.get("questions_to_confirm")) + '</article></section>'
    )


def _section_cards(title: str, subtitle: str, cards: list[str], grid_class: str = "card-grid") -> str:
    if not cards:
        return ""
    return f'<section class="content-section"><div class="section-head"><span>IMPLEMENTATION</span><h2>{_e(title)}</h2><p>{_e(subtitle)}</p></div><div class="{_a(grid_class)}">{"".join(cards)}</div></section>'


def _chips(value: Any) -> str:
    values = _strings(value)
    return '<div class="chips">' + ("".join(f'<span>{_e(item)}</span>' for item in values) if values else '<span>확인 필요</span>') + "</div>"


def _list_html(value: Any) -> str:
    values = _strings(value)
    return "<ul>" + ("".join(f'<li>{_e(item)}</li>' for item in values) if values else "<li>확인할 내용이 없습니다.</li>") + "</ul>"


def _safe_link(value: Any) -> str:
    url = _clean(value)
    if re.match(r"^https?://", url, flags=re.I):
        return f'<a href="{_a(url)}" target="_blank" rel="noopener noreferrer">설명서</a>'
    return f'<span>{_e(url)}</span>' if url else ""


def _security_passed(document: str) -> bool:
    if document.count('<script id="agent-ground-flow-runtime">') != 1 or document.count("</script>") != 1:
        return False
    without_runtime = re.sub(r'<script id="agent-ground-flow-runtime">.*?</script>', "", document, count=1, flags=re.S)
    blocked = ("<script", "javascript:", "<iframe", "<object", "<embed", " onerror=", " onload=")
    lowered = without_runtime.lower()
    return not any(token in lowered for token in blocked)


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _strings(value: Any, limit: int = 30) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "", {}) else [value])
    result = []
    for item in values:
        text = _clean(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _e(value: Any) -> str:
    return html.escape(_clean(value), quote=False)


def _a(value: Any) -> str:
    return html.escape(_clean(value), quote=True)


def _state(value: Any) -> str:
    state = _clean(value)
    return state if state in STATE_LABELS else "unchanged"


def _node_type(value: Any) -> str:
    node_type = _clean(value)
    return node_type if node_type in TYPE_LABELS else "process"


class SecureHtmlRenderer(Component):
    display_name = "06 HTML 업무 Flow 렌더링"
    description = "검증된 graph JSON을 분기선·변경 색상·개선 설명 버튼이 있는 안전한 BEFORE/AFTER HTML로 렌더링합니다."
    icon = "PanelsTopLeft"
    inputs = [DataInput(name="agent_design", display_name="AI Agent 설계 결과", required=True)]
    outputs = [Output(name="html_result", display_name="HTML 생성 결과", method="build_payload")]

    def build_payload(self) -> Data:
        result = render_business_design_html(getattr(self, "agent_design", None))
        security = _dict(_dict(result.get("html_result")).get("security_report"))
        self.status = {"보안 검사": "통과" if security.get("passed") else "확인 필요", "HTML 크기(bytes)": security.get("size_bytes"), "렌더링": "BEFORE/AFTER SVG flow chart"}
        return Data(data=result)


CSS = r"""
:root{--ink:#15223b;--muted:#66728a;--paper:#f4f7fb;--line:#cbd5e1;--navy:#172554;--blue:#2563eb;--mint:#059669;--orange:#d97706;--red:#dc2626;--white:#fff;--shadow:0 18px 48px rgba(20,36,68,.11)}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:linear-gradient(180deg,#eef4ff 0,#f8fafc 460px);color:var(--ink);font-family:Pretendard,"Noto Sans KR",system-ui,sans-serif;line-height:1.65}.skip-link{position:fixed;left:12px;top:-60px;z-index:99;background:#fff;padding:10px 16px;border-radius:10px}.skip-link:focus{top:12px}main{width:min(1560px,calc(100% - 32px));margin:auto;padding:28px 0 70px}.hero{padding:52px clamp(24px,5vw,72px);border-radius:30px;background:radial-gradient(circle at 80% 10%,rgba(96,165,250,.35),transparent 28%),linear-gradient(135deg,#0f172a,#172554 60%,#1e3a8a);color:#fff;box-shadow:var(--shadow)}.eyebrow,.section-head>span,.panel-head>span,.detail-number{font-size:.76rem;letter-spacing:.14em;font-weight:800;color:#93c5fd}.hero h1{font-size:clamp(2.15rem,4vw,4.4rem);line-height:1.08;margin:.55rem 0 1rem;max-width:900px}.hero p{font-size:1.08rem;color:#dbeafe;max-width:850px}.hero-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:26px}.hero-meta span{border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.08);padding:7px 12px;border-radius:999px}.legend,.content-section,.details-section,.notes-grid article{margin-top:24px;background:rgba(255,255,255,.9);border:1px solid #dbe3ef;border-radius:22px;padding:22px 24px;box-shadow:0 12px 30px rgba(39,55,89,.06)}.legend{display:flex;align-items:center;gap:24px}.legend h2{font-size:1rem;margin:0;white-space:nowrap}.legend ul{display:flex;flex-wrap:wrap;gap:18px;margin:0;padding:0;list-style:none}.legend li{display:flex;gap:7px;align-items:center;font-size:.88rem}.legend-dot{width:12px;height:12px;border-radius:4px}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}.chart-panel{min-width:0;background:#fff;border:1px solid #dbe3ef;border-radius:26px;overflow:hidden;box-shadow:var(--shadow)}.panel-head{padding:22px 24px 15px;border-bottom:1px solid #e5eaf2}.panel-head>span{color:#64748b}.theme-after .panel-head>span{color:var(--blue)}.panel-head h2{font-size:1.45rem;margin:2px 0}.panel-head p{margin:0;color:var(--muted);font-size:.85rem}.flowchart{position:relative;overflow:auto;min-height:660px;background-image:radial-gradient(#cbd5e1 1px,transparent 1px);background-size:18px 18px;background-color:#f8fafc}.node-layer,.connectors{position:absolute;inset:0}.connectors{overflow:visible;z-index:1}.flow-node{position:absolute;width:198px;z-index:2;filter:drop-shadow(0 10px 14px rgba(30,41,59,.12));outline:none}.node-shell{background:#fff;border:2px solid #94a3b8;border-radius:18px;padding:12px}.flow-node:focus .node-shell{outline:3px solid #93c5fd;outline-offset:3px}.flow-node.type-decision{width:216px}.flow-node.type-decision .node-shell{min-height:168px;clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%);padding:34px 38px;text-align:center;border-radius:0;background:#fffbeb}.flow-node.type-start .node-shell,.flow-node.type-end .node-shell{border-radius:999px;text-align:center}.node-top{display:flex;gap:6px;justify-content:space-between;align-items:center}.node-top span,.asset-tag{font-size:.66rem;line-height:1.3;font-weight:800;padding:3px 6px;border-radius:999px;background:#eef2f7}.state-label{color:#334155}.flow-node h3{font-size:.95rem;line-height:1.35;margin:8px 0 4px}.flow-node p{font-size:.74rem;line-height:1.45;color:var(--muted);margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.actor{font-size:.7rem;margin-top:8px;color:#475569}.asset-tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:7px}.asset-tag{color:#1d4ed8;background:#eff6ff}.detail-button{width:100%;margin-top:9px;border:0;border-radius:9px;padding:7px 9px;background:#172554;color:#fff;font:inherit;font-size:.75rem;font-weight:800;cursor:pointer}.detail-button:hover,.detail-button:focus{background:#2563eb}.state-unchanged .node-shell{border-color:#94a3b8}.state-modified .node-shell{border-color:var(--blue);background:#eff6ff}.state-added .node-shell{border-color:var(--mint);background:#ecfdf5}.state-human_review .node-shell{border-color:var(--orange);background:#fff7ed}.state-removed .node-shell{border-color:var(--red);background:#fef2f2;opacity:.82}.state-modified .state-label{background:#dbeafe;color:#1d4ed8}.state-added .state-label{background:#d1fae5;color:#047857}.state-human_review .state-label{background:#ffedd5;color:#c2410c}.state-removed .state-label{background:#fee2e2;color:#b91c1c}.legend-dot.state-unchanged{background:#94a3b8}.legend-dot.state-modified{background:var(--blue)}.legend-dot.state-added{background:var(--mint)}.legend-dot.state-human_review{background:var(--orange)}.legend-dot.state-removed{background:var(--red)}.edge-path{fill:none;stroke:#94a3b8;stroke-width:2.3}.edge-path.state-modified{stroke:var(--blue);stroke-width:3}.edge-path.state-added{stroke:var(--mint);stroke-width:3}.edge-path.state-human_review{stroke:var(--orange);stroke-width:3}.edge-path.state-removed{stroke:var(--red);stroke-dasharray:7 5}.edge-label{font-size:12px;font-weight:800;fill:#334155;paint-order:stroke;stroke:#fff;stroke-width:6px;stroke-linejoin:round}.accessible-flow{margin:0 18px 18px;padding:12px 14px;background:#f8fafc;border-radius:12px;color:#475569}.accessible-flow summary{cursor:pointer;font-weight:800}.accessible-flow li{margin:5px 0}.section-head{margin-bottom:18px}.section-head h2{font-size:clamp(1.55rem,2vw,2.2rem);margin:2px 0}.section-head p{margin:0;color:var(--muted)}.detail-panel{border:1px solid #cbd5e1;border-radius:18px;padding:24px;background:linear-gradient(145deg,#fff,#f8fbff)}.detail-panel[hidden]{display:none}.detail-panel h3{font-size:1.45rem;margin:4px 0 18px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px}.detail-grid>div{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px}.detail-grid h4{margin:0 0 6px;font-size:.9rem}.detail-grid p,.detail-grid ul{margin:0;color:#475569}.chips{display:flex;flex-wrap:wrap;gap:7px}.chips span{padding:5px 9px;border-radius:999px;background:#e0e7ff;color:#3730a3;font-size:.78rem;font-weight:800}.card-grid,.roadmap-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.capability-card,.roadmap-card{border:1px solid #e2e8f0;border-radius:16px;padding:18px;background:#fff}.capability-card h3,.roadmap-card h3{margin:4px 0 8px}.capability-card p,.roadmap-card p{color:#526079}.capability-id,.roadmap-card>span{font:700 .74rem ui-monospace,monospace;color:#2563eb}.source-links{display:flex;gap:8px;margin-top:12px}.source-links a,.source-links span{font-size:.78rem;color:#2563eb}.success{border-top:1px solid #e2e8f0;padding-top:10px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:680px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e2e8f0;vertical-align:top}thead th{background:#f1f5f9}.notes-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.notes-grid h2{margin-top:0;font-size:1.2rem}.empty{color:var(--muted)}@media(max-width:1050px){.comparison{grid-template-columns:1fr}.flowchart{min-height:620px}.card-grid,.roadmap-grid{grid-template-columns:1fr 1fr}}@media(max-width:680px){main{width:min(100% - 18px,1560px);padding-top:10px}.hero{border-radius:20px;padding:34px 20px}.legend{align-items:flex-start;flex-direction:column;gap:10px}.comparison{gap:12px}.chart-panel{border-radius:18px}.flowchart{min-height:600px}.detail-grid,.card-grid,.roadmap-grid,.notes-grid{grid-template-columns:1fr}.content-section,.details-section,.legend,.notes-grid article{padding:18px 16px}.flow-node{width:176px}.flow-node.type-decision{width:190px}}
"""

CSS += r"""
.report-nav{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.report-nav button,.report-nav a{border:1px solid #cbd5e1;border-radius:999px;background:#fff;color:#172554;padding:8px 14px;font:inherit;font-size:.82rem;font-weight:800;text-decoration:none;cursor:pointer}.report-nav button:hover,.report-nav button:focus,.report-nav a:hover,.report-nav a:focus{border-color:#2563eb;color:#2563eb}
"""


SCRIPT = r"""
(function(){'use strict';
function svg(name,attrs){var el=document.createElementNS('http://www.w3.org/2000/svg',name);Object.keys(attrs||{}).forEach(function(k){el.setAttribute(k,String(attrs[k]));});return el;}
function layout(chart,chartIndex){var nodes=Array.from(chart.querySelectorAll('.flow-node'));var metas=Array.from(chart.querySelectorAll('.edge-meta'));if(!nodes.length){return;}var byId={};nodes.forEach(function(n){byId[n.dataset.nodeId]=n;});var indegree={};var outgoing={};nodes.forEach(function(n){indegree[n.dataset.nodeId]=0;outgoing[n.dataset.nodeId]=[];});metas.forEach(function(e){if(byId[e.dataset.source]&&byId[e.dataset.target]){outgoing[e.dataset.source].push(e);indegree[e.dataset.target]+=1;}});var queue=Object.keys(indegree).filter(function(id){return indegree[id]===0;});var level={};queue.forEach(function(id){level[id]=0;});while(queue.length){var id=queue.shift();outgoing[id].forEach(function(e){level[e.dataset.target]=Math.max(level[e.dataset.target]||0,(level[id]||0)+1);indegree[e.dataset.target]-=1;if(indegree[e.dataset.target]===0){queue.push(e.dataset.target);}});}nodes.forEach(function(n,i){if(level[n.dataset.nodeId]===undefined){level[n.dataset.nodeId]=i;}});var groups={};nodes.forEach(function(n){var l=level[n.dataset.nodeId];(groups[l]||(groups[l]=[])).push(n);});var maxGroup=Math.max.apply(null,Object.keys(groups).map(function(k){return groups[k].length;}));var compact=window.innerWidth<680;var nodeWidth=compact?176:198;var gapX=compact?34:52;var width=Math.max(compact?520:720,maxGroup*nodeWidth+(maxGroup+1)*gapX);var gapY=compact?210:195;var top=38;Object.keys(groups).forEach(function(k){var group=groups[k];var total=group.length*nodeWidth+(group.length-1)*gapX;var left=(width-total)/2;group.forEach(function(n,i){n.style.left=(left+i*(nodeWidth+gapX))+'px';n.style.top=(top+Number(k)*gapY)+'px';});});var maxLevel=Math.max.apply(null,Object.values(level));var height=Math.max(580,top+(maxLevel+1)*gapY+70);var layer=chart.querySelector('.node-layer');layer.style.width=width+'px';layer.style.height=height+'px';chart.style.minWidth=Math.min(width,720)+'px';chart.style.height=height+'px';var board=chart.querySelector('.connectors');board.setAttribute('viewBox','0 0 '+width+' '+height);board.setAttribute('width',width);board.setAttribute('height',height);board.innerHTML='';var defs=svg('defs');var marker=svg('marker',{id:'arrow-'+chartIndex,markerWidth:9,markerHeight:9,refX:8,refY:3,orient:'auto',markerUnits:'strokeWidth'});marker.appendChild(svg('path',{d:'M0,0 L0,6 L8,3 z',fill:'context-stroke'}));defs.appendChild(marker);board.appendChild(defs);metas.forEach(function(e){var s=byId[e.dataset.source],t=byId[e.dataset.target];if(!s||!t){return;}var sx=s.offsetLeft+s.offsetWidth/2,sy=s.offsetTop+s.offsetHeight;var tx=t.offsetLeft+t.offsetWidth/2,ty=t.offsetTop;var bend=Math.max(45,(ty-sy)/2);var path=svg('path',{d:'M '+sx+' '+sy+' C '+sx+' '+(sy+bend)+' '+tx+' '+(ty-bend)+' '+tx+' '+ty,'class':'edge-path state-'+(e.dataset.state||'unchanged'),'marker-end':'url(#arrow-'+chartIndex+')'});board.appendChild(path);var label=e.dataset.label||'';if(label){var text=svg('text',{x:(sx+tx)/2,y:(sy+ty)/2-7,'text-anchor':'middle','class':'edge-label'});text.textContent=label;board.appendChild(text);}});}
function layoutAll(){Array.from(document.querySelectorAll('.flowchart')).forEach(layout);}
var timer;window.addEventListener('resize',function(){clearTimeout(timer);timer=setTimeout(layoutAll,120);});window.addEventListener('load',layoutAll);layoutAll();
var buttons=Array.from(document.querySelectorAll('.detail-button'));var panels=Array.from(document.querySelectorAll('[data-detail-panel]'));buttons.forEach(function(button){button.addEventListener('click',function(){var id=button.dataset.detailId;panels.forEach(function(panel){panel.hidden=panel.dataset.detailPanel!==id;});buttons.forEach(function(item){item.setAttribute('aria-expanded',String(item===button));});var target=document.getElementById('detail-'+id);if(target){target.focus({preventScroll:true});target.scrollIntoView({behavior:'smooth',block:'center'});}});});
var back=document.getElementById('reportBack');if(back){back.addEventListener('click',function(){if(window.history.length>1){window.history.back();}else{window.scrollTo({top:0,behavior:'smooth'});}});}
})();
"""
