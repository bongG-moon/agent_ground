from __future__ import annotations

"""Render a deterministic self-contained report; no LLM-generated HTML is executed."""

import base64
import hashlib
import html
import json
import math
import re
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, MultilineInput, Output, StrInput
from lfx.schema import Data


RENDERER_VERSION = "business-report-renderer.v1"
F30_TERMINAL_SCHEMA_VERSION = "f30-terminal-result/v1"
NODE_KINDS = {
    "start",
    "end",
    "work_step",
    "decision",
    "human_gate",
    "system_call",
    "new_custom",
    "companion_service",
    "skill_group",
    "exception",
}
IMPLEMENTATION_SOURCES = {
    "builtin",
    "catalog_component",
    "catalog_flow",
    "new_standalone_component",
    "companion_service",
    "human_task",
}
CONNECTION_STATUSES = {"unverified", "contract_compatible", "verified_runtime"}
TOP_LEVEL_KEYS = {
    "schema_version", "renderer_version", "report_id", "title", "summary", "as_is_graph",
    "to_be_graph", "sections", "retrieval_trace", "source_contract_hash",
}
SUMMARY_KEYS = {
    "work_definition_id", "work_definition_revision", "approval_status", "approved_hash", "blueprint_id",
    "blueprint_sha256", "catalog_snapshot_id", "pattern", "pattern_reason", "build_readiness",
}
GRAPH_KEYS = {
    "graph_id", "graph_kind", "build_readiness", "layout_direction", "nodes", "edges", "groups",
    "details", "generation_requests", "text_fallback",
}
NODE_KEYS = {
    "node_id", "source_node_id", "node_kind", "title", "sequence", "implementation_source",
    "implementation_label", "technical_contract_status", "port_contract_sha256", "summary", "input_ports", "output_ports",
    "applied_skills", "detail_ref", "generation_request_ref",
}
EDGE_KEYS = {
    "edge_id", "source_node_id", "source_port_id", "target_node_id", "target_port_id", "edge_kind",
    "label", "condition", "is_default", "connection_validation_status", "mapping", "retry_policy",
}
PORT_KEYS = {
    "port_id", "source_port_id", "label", "name", "data_type", "semantic_role", "schema_ref",
    "required", "cardinality", "has_default", "secret", "permission", "network_zone", "streaming",
}
SKILL_KEYS = {"skill_id", "name", "version", "prompt_sha256", "match_reason", "target_stage", "source_ref"}
DETAIL_KEYS = {
    "title", "current_work", "problems", "improvement", "reuse_decision_reason", "asset_ref", "inputs",
    "outputs", "config", "secrets_permissions", "failure_policy", "human_review", "tests", "applied_skills",
}
REQUEST_KEYS = {
    "generation_request_id", "target_node_id", "template_version", "prompt_pack", "component_filename",
    "class_name", "prompt_sha256", "request_text",
}
SECTION_KEYS = {"section_id", "title", "items"}
EDGE_KINDS = {"control", "data", "branch", "human", "retry", "error"}
TECHNICAL_STATUSES = {None, "metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
BUILD_READINESS = {"design_only", "proposed_unverified", "import_ready"}
PROMPT_PACKS = {"CCP-CATALOG", "CCP-WORK", "CCP-SEARCH-SKILL", "CCP-BLUEPRINT", "CCP-REPORT"}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
GENERATION_REQUEST_ID_PATTERN = re.compile(r"^gen-[0-9a-f]{20}$")
REPORT_ID_PATTERN = re.compile(r"^report-[0-9a-f]{24}$")
COMPONENT_FILENAME_PATTERN = re.compile(r"^[0-9]{2}_[a-z][a-z0-9_]{1,80}\.py$")
COMPONENT_CLASS_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]{2,100}Component$")
SECRET_KEY_TOKENS = {
    "apikey", "authorization", "clientsecret", "cookie", "credential", "password", "passwd",
    "privatekey", "pwd", "session", "smsession", "secret", "token",
}
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie|session)\s*[:=]\s*[\"']?[^\s,;]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_allowed_hosts(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("allowed_hosts_json must contain at most 100 host strings")
    if _secret_material_kind(value):
        raise ValueError("REPORT_SECRET_MATERIAL_DETECTED: allowed_hosts_json contains secret material")
    result: list[str] = []
    for item in value:
        if type(item) is not str:
            raise ValueError("allowed_hosts_json must contain host strings")
        host = item.strip().lower()
        if not host or len(host) > 253 or host != item.strip().casefold():
            raise ValueError("allowed_hosts_json contains an invalid host")
        labels = host.split(".")
        is_ipv4 = len(labels) == 4 and all(label.isdigit() for label in labels)
        if is_ipv4:
            valid = all(str(int(label)) == label and 0 <= int(label) <= 255 for label in labels)
        else:
            valid = host == "localhost" or (
                len(labels) >= 2 and all(HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
            )
        if not valid:
            raise ValueError("allowed_hosts_json contains an invalid host")
        if host in result:
            raise ValueError("allowed_hosts_json contains a duplicate host")
        result.append(host)
    return result


CSS = r"""
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#7a808c;--line:#e8eaee;--orange:#ff5a1f;--purple:#7257e8;--green:#20a47a;--amber:#e79b31;--shadow:0 10px 30px rgba(20,24,32,.06)}*{box-sizing:border-box}html{scrollbar-color:#c9cdd5 transparent;scrollbar-width:thin}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Noto Sans KR","Segoe UI",sans-serif;letter-spacing:-.02em}.shell{max-width:1540px;margin:auto;padding:30px 34px 70px}.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px}.brand{display:flex;align-items:center;gap:10px;font-weight:800}.brand-mark{width:30px;height:30px;border-radius:10px;background:var(--orange);display:grid;place-items:center;color:#fff;font-size:14px}.meta{font-size:12px;color:var(--muted)}.intro{display:grid;grid-template-columns:minmax(280px,.62fr) minmax(520px,1.38fr);gap:14px;margin-bottom:18px}.intro-main,.intro-side{background:#fff;border:1px solid var(--line);border-radius:22px;box-shadow:var(--shadow)}.intro-main{padding:20px 22px;display:flex;flex-direction:column;justify-content:center}.intro-side{padding:22px 24px;border-color:#ffded1;background:linear-gradient(135deg,#fff 0%,#fffaf7 100%);position:relative;overflow:hidden}.intro-side:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--orange)}.eyebrow{font-size:11px;font-weight:800;letter-spacing:.13em;color:var(--orange);text-transform:uppercase}.intro h1{font-size:23px;line-height:1.25;margin:7px 0 7px}.intro-main .eyebrow{font-size:9px}.intro-main p{font-size:12px;line-height:1.55}.intro p{margin:0;color:var(--muted);line-height:1.65}.status{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:700}.dot{width:8px;height:8px;border-radius:50%;background:var(--green)}.side-title{font-size:11px;color:var(--muted);margin-bottom:8px}.intro-side .side-title[style]{margin-top:14px!important;color:var(--orange);font-size:12px;font-weight:850;letter-spacing:.02em}.side-reason{font-size:16px;line-height:1.6;font-weight:760;color:#27221f;padding:13px 15px;background:#fff;border:1px solid #ffe2d6;border-radius:14px;box-shadow:0 6px 18px rgba(255,90,31,.06)}.tabs{display:flex;gap:8px;margin:22px 0 12px;padding:5px;background:#ebeef2;border-radius:15px;width:max-content}.tab{border:0;background:transparent;padding:10px 16px;border-radius:11px;color:#858b95;font-weight:750;cursor:pointer;transition:.18s}.tab[data-tab="as_is"]{font-size:12px}.tab[data-tab="to_be"]{font-size:14px;color:#d94b17;font-weight:850}.tab.active{background:#fff;color:#343840;box-shadow:0 3px 10px rgba(20,24,32,.08)}.tab[data-tab="to_be"].active{background:var(--orange);color:#fff;box-shadow:0 5px 16px rgba(255,90,31,.24)}.flow-panel{background:#fff;border:1px solid var(--line);border-radius:26px;box-shadow:var(--shadow);overflow:hidden;margin-bottom:18px}.flow-head{display:flex;justify-content:space-between;align-items:flex-end;padding:24px 26px 15px}.flow-kicker{font-size:11px;font-weight:800;letter-spacing:.12em;color:var(--orange)}.flow-head h2{margin:5px 0 4px;font-size:22px}.flow-head p{margin:0;color:var(--muted);font-size:13px}.legend{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.pill{padding:6px 9px;border-radius:999px;background:#f4f5f7;color:#707681;font-size:11px;font-weight:700}.pill.new{background:#fff0e9;color:#e84b12}.pill.skill{background:#f1edff;color:#6645dc}.graph-frame{height:360px;position:relative;border-top:1px solid #f0f1f3;background:#fcfcfd;overflow:hidden}.graph-viewport{position:absolute;inset:0;overflow:auto;cursor:grab;scrollbar-width:thin;scrollbar-color:#cfd3da transparent}.graph-viewport::-webkit-scrollbar{height:7px;width:7px}.graph-viewport::-webkit-scrollbar-track{background:transparent}.graph-viewport::-webkit-scrollbar-thumb{background:#cfd3da;border-radius:99px;border:2px solid transparent;background-clip:padding-box}.graph-viewport::-webkit-scrollbar-thumb:hover{background:#aeb4bf;background-clip:padding-box}.graph-world{position:relative;transform-origin:0 0}.edge-layer,.node-layer{position:absolute;inset:0}.edge-layer{overflow:visible;pointer-events:none}.edge-path{fill:none;stroke:#b8bec8;stroke-width:2}.edge-hit{fill:none;stroke:transparent;stroke-width:18;pointer-events:stroke}.edge-label{position:absolute;z-index:4;transform:translate(-50%,-50%);border:1px solid #e7e9ed;background:#fff;box-shadow:0 3px 10px rgba(20,24,32,.04);border-radius:8px;padding:4px 6px;width:68px;white-space:normal;word-break:keep-all;overflow-wrap:break-word;text-align:center;line-height:1.18;font-size:9px;color:#7b818c;cursor:pointer}.flow-node{position:absolute;width:214px;min-height:138px;background:#fff;border:1px solid #e6e8ec;border-radius:18px;box-shadow:0 8px 22px rgba(20,24,32,.06);overflow:hidden;transition:.18s}.flow-node:hover{transform:translateY(-2px);box-shadow:0 12px 26px rgba(20,24,32,.09)}.flow-node:before{content:"";position:absolute;left:16px;right:16px;top:0;height:4px;border-radius:0 0 5px 5px;background:#aab1bc}.flow-node.new_custom:before{background:var(--orange)}.flow-node.human_gate:before{background:var(--amber)}.flow-node.start:before,.flow-node.end:before{background:var(--green)}.flow-node.companion_service:before{background:#66758b}.node-main{width:100%;min-height:138px;border:0;background:transparent;text-align:left;padding:17px 16px 14px;cursor:pointer;color:inherit}.node-top{display:flex;justify-content:space-between;color:#9aa0aa;font-size:10px}.node-main h3{font-size:15px;line-height:1.35;margin:11px 0 6px}.node-main p{font-size:11.5px;color:#777e89;line-height:1.45;margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.node-badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:10px}.node-badges .pill{padding:5px 7px;font-size:10px}.toolbar{position:absolute;z-index:10;right:16px;top:14px;display:flex;align-items:center;background:#fff;border:1px solid #e7e9ed;border-radius:13px;padding:4px;box-shadow:0 7px 22px rgba(20,24,32,.08)}.toolbar button,.zoom-readout{height:30px;border:0;background:transparent;color:#686e79;font-size:12px;display:grid;place-items:center}.toolbar button{width:31px;border-radius:8px;cursor:pointer}.toolbar button:hover{background:#f3f4f6}.zoom-readout{min-width:52px;padding:0 6px;font-variant-numeric:tabular-nums}.drawer-backdrop{position:fixed;inset:0;background:rgba(15,17,22,.18);opacity:0;pointer-events:none;transition:.2s;z-index:19}.drawer-backdrop.open{opacity:1;pointer-events:auto}.drawer{position:fixed;z-index:20;right:16px;top:16px;bottom:16px;width:min(470px,calc(100vw - 32px));background:#fff;border-radius:24px;box-shadow:0 24px 70px rgba(18,21,29,.2);transform:translateX(calc(100% + 40px));transition:.22s;overflow:auto;scrollbar-width:thin;scrollbar-color:#d1d5dc transparent}.drawer.open{transform:none}.drawer-head{position:sticky;top:0;z-index:2;background:rgba(255,255,255,.94);backdrop-filter:blur(12px);padding:22px 22px 14px;border-bottom:1px solid #f0f1f3}.drawer-head-row{display:flex;justify-content:space-between;gap:12px}.drawer h2{font-size:20px;margin:5px 0 0}.close{width:34px;height:34px;border:0;border-radius:10px;background:#f3f4f6;cursor:pointer;font-size:18px}.drawer-body{padding:10px 22px 24px}.detail{padding:15px 0;border-bottom:1px solid #f0f1f3}.detail h3{font-size:11px;color:#8b919c;margin:0 0 7px}.detail .value{font-size:13px;line-height:1.6;color:#343840;white-space:pre-wrap;overflow-wrap:anywhere}.support{background:#fff;border:1px solid var(--line);border-radius:24px;padding:22px 26px;box-shadow:var(--shadow)}.support h2{font-size:18px;margin:0 0 10px}.support details{border-top:1px solid #eff0f2;padding:13px 0}.support summary{cursor:pointer;font-size:13px;font-weight:700}.support pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f8f9;padding:12px;border-radius:12px;font:12px/1.5 inherit;color:#555b66}.static-fallback{display:none}@media(max-width:850px){.shell{padding:16px}.intro{grid-template-columns:1fr}.flow-head{align-items:flex-start;gap:12px;flex-direction:column}.legend{justify-content:flex-start}.graph-frame{height:420px}.intro h1{font-size:26px}}
.quick-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.quick-meta span{font-size:12px;padding:7px 10px;border:1px solid #e7e9ee;border-radius:10px;background:#fafbfc;color:#747b88}.quick-meta b{color:#20242b;margin-left:5px}.support-list{padding:4px 0 10px}.support-item{padding:12px 2px;border-top:1px solid #eef0f3}.support-item:first-child{border-top:0}.support-item strong{font-size:13px;color:#252a32}.support-item p{margin:5px 0 0;font-size:12px;line-height:1.55;color:#6f7784}
/* v6: quieter, editorial report chrome; flow canvas intentionally unchanged */
body{background:#f3f4f5}.shell{padding-top:22px}.topbar{margin-bottom:18px;padding:0 2px 14px;border-bottom:1px solid #dde0e4}.brand{gap:8px;font-size:14px}.brand-mark{width:24px;height:24px;border-radius:6px;background:#25282d;font-size:11px}.meta{color:#9297a0}.intro{display:grid;grid-template-columns:minmax(0,1fr);gap:0;margin:0 0 20px;background:#fff;border:1px solid #e2e4e8;border-radius:18px;overflow:hidden;box-shadow:none}.intro-main,.intro-side{border:0;border-radius:0;box-shadow:none;background:#fff}.intro-main{padding:24px 28px 18px;border-bottom:1px solid #eceef1}.intro-main .eyebrow{display:none}.intro h1{font-size:22px;margin:0 0 6px;letter-spacing:-.035em}.intro-main p{max-width:760px;color:#737983}.intro-side{padding:0;display:grid;grid-template-columns:190px minmax(0,1fr);align-items:stretch;overflow:visible}.intro-side:before{display:none}.intro-side>.side-title:first-child{display:none}.status{padding:18px 22px;border-right:1px solid #eceef1;font-size:12px;align-content:center}.quick-meta{position:absolute;right:62px;top:31px;margin:0}.quick-meta span{border:0;background:#f3f4f6;padding:5px 8px;border-radius:6px;font-size:11px}.intro-side .side-title[style]{margin:0!important;padding:17px 18px 4px;color:#25282d;font-size:13px;font-weight:800;letter-spacing:-.02em}.side-reason{grid-column:2;padding:0 18px 18px;background:transparent;border:0;border-radius:0;box-shadow:none;font-size:14px;line-height:1.55;font-weight:600;color:#4a4f57}.tabs{margin:0 0 10px;padding:0;background:transparent;border-radius:0;border-bottom:1px solid #dfe2e6;width:100%;gap:24px}.tab{padding:11px 2px 10px;border-radius:0;font-size:13px!important;color:#9297a0!important}.tab.active{background:transparent!important;box-shadow:none!important;color:#25282d!important;border-bottom:2px solid #25282d}.tab[data-tab="to_be"]{color:#555b64!important}.tab[data-tab="to_be"].active{color:#e64d18!important;border-bottom-color:#f05a28}.support{margin-top:22px;border-radius:16px;box-shadow:none;padding:0;background:transparent;border:0}.support h2{font-size:14px;margin:0 0 9px;padding-left:2px}.support-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.support-item{background:#fff;border:1px solid #e3e5e8!important;border-radius:12px;padding:14px 16px!important}.support-item strong{font-size:12px}.support-item p{font-size:11.5px}.drawer .eyebrow{display:none}.drawer h2{margin-top:0}.flow-kicker{display:none}@media(max-width:850px){.intro-side{grid-template-columns:1fr}.status{border-right:0;border-bottom:1px solid #eceef1}.intro-side .side-title[style],.side-reason{grid-column:1}.quick-meta{position:static;padding:0 22px 14px}.support-list{grid-template-columns:1fr}}
/* Preserve no-JS, reduced-motion, and print access promised by the renderer contract. */
.static-fallback{display:block}
.js .static-fallback{display:none}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
@media print{body{background:#fff}.topbar,.tabs,.graph-frame,.toolbar,.drawer,.drawer-backdrop{display:none!important}.shell{max-width:none;padding:0}.intro,.support{box-shadow:none;break-inside:avoid}.static-fallback{display:block!important}}

"""


JS = r"""
(()=>{'use strict';document.documentElement.classList.add('js');const vm=JSON.parse(document.getElementById('report-data').textContent);const $=s=>document.querySelector(s);$('#page-title').textContent=vm.title||'업무 흐름을 더 단순하게, Agent와 함께.';$('#page-desc').textContent='반복 작업은 Agent가 처리하고, 중요한 판단은 사람이 담당하는 업무 구조를 한눈에 확인하세요.';$('#reason').textContent=vm.summary?.pattern_reason||'';$('#approval-meta').textContent=vm.summary?.approval_status==='APPROVED'?'승인됨':(vm.summary?.approval_status||'-');$('#revision-meta').textContent='Rev. '+(vm.summary?.work_definition_revision??'-');
const friendly={current_work:'현재는 이렇게 해요',problems:'불편한 점',improvement:'Agent 적용 후',failure_policy:'문제가 생기면',inputs:'받는 정보',outputs:'만드는 결과',human_review:'사람이 확인하는 부분',applied_skills:'사용하는 AI 기능',reuse_decision_reason:'이 방식을 선택한 이유',secrets_permissions:'필요 권한',tests:'확인할 항목',config:'설정',asset_ref:'연결된 기존 자산',implementation_source:'구현 방식',technical_contract_status:'연결 상태',generation_request:'개발 참고 정보',title:'단계 이름'};const hidden=new Set(['title','config','asset_ref','secrets_permissions','technical_contract_status','generation_request']);
const fmt=v=>{if(typeof v==='string')return v;if(Array.isArray(v))return v.map(x=>typeof x==='string'?'• '+x:'• '+(x.description||x.name||x.label||JSON.stringify(x))).join('\n');if(typeof v==='object'&&v){if(Object.keys(v).length===0)return '';if(v.error_code)return `오류가 발생하면 작업을 중단하고 원인을 표시합니다. (${v.error_code})`;return Object.entries(v).map(([k,x])=>`${k}: ${typeof x==='object'?JSON.stringify(x):x}`).join('\n')}return String(v??'')};
const drawer=$('#drawer'),backdrop=$('#backdrop');function openDrawer(title,detail){$('#drawer-title').textContent=title;const body=$('#drawer-body');body.innerHTML='';Object.entries(detail||{}).forEach(([k,v])=>{if(hidden.has(k)||v==null||v===''||(Array.isArray(v)&&!v.length))return;const text=fmt(v);if(!text)return;const d=document.createElement('section');d.className='detail';d.innerHTML=`<h3>${friendly[k]||k}</h3><div class="value"></div>`;d.querySelector('.value').textContent=text;body.append(d)});drawer.classList.add('open');backdrop.classList.add('open')}function close(){drawer.classList.remove('open');backdrop.classList.remove('open')}$('#close').onclick=close;backdrop.onclick=close;document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
const labels={Human:'사람이 수행','기본 요소':'기본 기능','신규 Custom':'새로 만드는 기능','외부 서비스':'연결 서비스'};function render(kind){const graph=kind==='as_is'?vm.as_is_graph:vm.to_be_graph;$('#flow-kicker').textContent=kind==='as_is'?'CURRENT WORK':'WITH AGENT';$('#flow-title').textContent=kind==='as_is'?'현재 업무 흐름':'Agent 적용 후 업무 흐름';$('#flow-desc').textContent=kind==='as_is'?'지금 사람이 직접 수행하는 과정을 보여줍니다.':'반복 작업은 Agent가 처리하고, 중요한 판단은 사람이 담당합니다.';const legend=$('#legend');legend.innerHTML='';[...new Set(graph.nodes.map(n=>n.implementation_label))].forEach(x=>{const s=document.createElement('span');s.className='pill'+(x==='신규 Custom'?' new':'');s.textContent=labels[x]||x;legend.append(s)});if(graph.nodes.some(n=>(n.applied_skills||[]).length)){const s=document.createElement('span');s.className='pill skill';s.textContent='AI Skill';legend.append(s)};const host=$('#graph-host');host.innerHTML='';renderGraph(graph,host)}
function renderGraph(graph,host){const frame=document.createElement('div');frame.className='graph-frame';const vp=document.createElement('div');vp.className='graph-viewport';const world=document.createElement('div');world.className='graph-world';const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('edge-layer');const nl=document.createElement('div');nl.className='node-layer';world.append(svg,nl);vp.append(world);frame.append(vp);const tb=document.createElement('div');tb.className='toolbar';const minus=document.createElement('button'),read=document.createElement('div'),plus=document.createElement('button'),fit=document.createElement('button');minus.textContent='−';plus.textContent='+';fit.textContent='↙';fit.title='전체 보기';read.className='zoom-readout';tb.append(minus,read,plus,fit);frame.append(tb);host.append(frame);
const W=214,H=138,gap=118,left=54,top=92;const pos=new Map();graph.nodes.forEach((n,i)=>pos.set(n.node_id,{x:left+i*(W+gap),y:top}));const maxX=left*2+(graph.nodes.length-1)*(W+gap)+W,maxY=330;world.style.width=maxX+'px';world.style.height=maxY+'px';svg.setAttribute('width',maxX);svg.setAttribute('height',maxY);svg.setAttribute('viewBox',`0 0 ${maxX} ${maxY}`);let scale=1;function apply(){world.style.transform=`scale(${scale})`;read.textContent=Math.round(scale*100)+'%'}function zoom(d){scale=Math.max(.55,Math.min(1.6,+(scale+d).toFixed(2)));apply()}function fitAll(){scale=Math.max(.55,Math.min(1,(vp.clientWidth-36)/maxX));apply();vp.scrollTo({left:0,top:0})}minus.onclick=()=>zoom(-.1);plus.onclick=()=>zoom(.1);fit.onclick=fitAll;
const defs=document.createElementNS('http://www.w3.org/2000/svg','defs'),marker=document.createElementNS('http://www.w3.org/2000/svg','marker');marker.id='arrow-'+graph.graph_id;[['viewBox','0 0 10 10'],['refX','9'],['refY','5'],['markerWidth','6'],['markerHeight','6'],['orient','auto']].forEach(([a,v])=>marker.setAttribute(a,v));const ap=document.createElementNS('http://www.w3.org/2000/svg','path');ap.setAttribute('d','M0 0 L10 5 L0 10z');ap.setAttribute('fill','#b8bec8');marker.append(ap);defs.append(marker);svg.append(defs);
graph.edges.forEach(e=>{const s=pos.get(e.source_node_id),t=pos.get(e.target_node_id),sx=s.x+W,sy=s.y+H/2,tx=t.x,ty=t.y+H/2,mid=(sx+tx)/2;const d=`M${sx} ${sy} H${tx}`;const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',d);p.classList.add('edge-path');p.setAttribute('marker-end',`url(#arrow-${graph.graph_id})`);svg.append(p);const b=document.createElement('button');b.className='edge-label';b.textContent=e.label||'다음';const available=Math.max(52,tx-sx-18);b.style.width=Math.min(86,available)+'px';b.style.left=mid+'px';b.style.top=sy+'px';b.onclick=()=>openDrawer(e.label||'연결 정보',{'현재 단계':graph.nodes.find(n=>n.node_id===e.source_node_id)?.title,'다음 단계':graph.nodes.find(n=>n.node_id===e.target_node_id)?.title,condition:e.condition});nl.append(b)});
graph.nodes.forEach(n=>{const p=pos.get(n.node_id),a=document.createElement('article');a.className='flow-node '+(n.node_kind||'');a.style.left=p.x+'px';a.style.top=p.y+'px';const b=document.createElement('button');b.className='node-main';const type=labels[n.implementation_label]||n.implementation_label;b.innerHTML=`<div class="node-top"><span>업무 단계</span><span>STEP ${n.sequence}</span></div><h3></h3><p></p><div class="node-badges"></div>`;b.querySelector('h3').textContent=n.title;b.querySelector('p').textContent=n.summary||((graph.details||{})[n.detail_ref]?.current_work)||'';const badges=b.querySelector('.node-badges');const p1=document.createElement('span');p1.className='pill'+(n.implementation_label==='신규 Custom'?' new':'');p1.textContent=type;badges.append(p1);if((n.applied_skills||[]).length){const sk=document.createElement('span');sk.className='pill skill';sk.textContent='AI Skill 적용';badges.append(sk)}b.onclick=()=>{const detail={...(graph.details||{})[n.detail_ref],implementation_source:type};openDrawer(n.title,detail)};a.append(b);nl.append(a)});apply();
let drag=false,x=0,y=0,sl=0,st=0;vp.onpointerdown=e=>{if(e.target.closest('button'))return;drag=true;x=e.clientX;y=e.clientY;sl=vp.scrollLeft;st=vp.scrollTop;vp.setPointerCapture(e.pointerId)};vp.onpointermove=e=>{if(drag){vp.scrollLeft=sl-(e.clientX-x);vp.scrollTop=st-(e.clientY-y)}};vp.onpointerup=()=>drag=false;vp.addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();zoom(e.deltaY<0?.1:-.1)}},{passive:false})}
$('.tabs').addEventListener('click',e=>{const b=e.target.closest('.tab');if(!b)return;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));render(b.dataset.tab)});const sup=$('#support');(vm.sections||[]).forEach(s=>{const d=document.createElement('details');const sm=document.createElement('summary');sm.textContent=s.title;d.append(sm);const box=document.createElement('div');box.className='support-list';if(!(s.items||[]).length){box.textContent='현재 추가 확인 사항이 없습니다.'}else{(s.items||[]).forEach(item=>{const row=document.createElement('div');row.className='support-item';const title=document.createElement('strong');title.textContent=item.name||item.description||item.test_id||'항목';const desc=document.createElement('p');desc.textContent=item.risk&&item.control?`주의: ${item.risk} · 대응: ${item.control}`:(item.description||item.control||'');row.append(title);if(desc.textContent)row.append(desc);box.append(row)})}d.append(box);sup.append(d)});render('to_be')})();
"""


def _raw(value: Any) -> Any:
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return data
    return value


def _view_model(value: Any) -> dict[str, Any]:
    value = _raw(value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("report_view_model must be JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("report_view_model must be an object")
    if "ok" in value and (value.get("ok") is not True or value.get("status") != "COMPLETED"):
        raise ValueError("report_view_model upstream envelope is not successful")
    if isinstance(value.get("report_view_model"), dict):
        value = value["report_view_model"]
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _csp_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def _text(value: Any, limit: int = 20_000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return str(value)[:limit]


def _f30_terminal_failure(
    *,
    stage: str,
    code: str,
    message: str,
    upstream: Any = None,
) -> dict[str, Any]:
    """Return a safe F30 terminal envelope instead of a child-flow exception."""

    source = _raw(upstream)
    source_error = source.get("error") if isinstance(source, dict) else None
    if isinstance(source_error, dict):
        source_code = _text(source_error.get("code"), 128).strip()
        source_message = _text(source_error.get("message"), 500).strip()
        if source_code:
            code = source_code
        if source_message:
            message = source_message
    trace_id = _text(source.get("trace_id"), 200).strip() if isinstance(source, dict) else ""
    if IDENTITY_PATTERN.fullmatch(trace_id) is None:
        digest = hashlib.sha256(f"{stage}:{code}".encode("utf-8")).hexdigest()[:24]
        trace_id = f"trace-f30-{digest}"
    return {
        "ok": False,
        "status": "BLOCKED",
        "schema_version": F30_TERMINAL_SCHEMA_VERSION,
        "stage": stage,
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "details": {},
        },
        "trace_id": trace_id,
    }


def _upstream_f30_failure(value: Any, *, stage: str) -> dict[str, Any] | None:
    source = _raw(value)
    if not isinstance(source, dict) or source.get("ok") is not False:
        return None
    return _f30_terminal_failure(
        stage=stage,
        code="F30_UPSTREAM_BLOCKED",
        message="F30 이전 단계에서 보고서 생성을 중단했습니다.",
        upstream=source,
    )


def _escaped_json(value: dict[str, Any]) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _expected_report_id(view_model: dict[str, Any]) -> str:
    identity = {key: value for key, value in view_model.items() if key != "report_id"}
    material = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "report-" + _sha256_text(material)[:24]


def _ensure_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string object key")
            _ensure_json_value(item, f"{path}.<field>")
        return
    raise ValueError(f"{path} contains a non-JSON value")


def _allowed_keys(value: Any, allowed: set[str], path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    unexpected_count = sum(1 for key in value if key not in allowed)
    if unexpected_count:
        raise ValueError(f"{path} contains unsupported fields ({unexpected_count})")


def _required_keys(value: dict[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")


def _string(value: Any, path: str, *, minimum: int = 0, maximum: int, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or len(value) < minimum or len(value) > maximum:
        raise ValueError(f"{path} must be a string between {minimum} and {maximum} characters")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{path} has an invalid format")
    return value


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{path} must be an integer >= {minimum}")
    return value


def _array(value: Any, path: str, *, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{path} must be an array with at most {maximum} items")
    return value


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _validate_port(port: Any, path: str) -> None:
    port = _object(port, path)
    _allowed_keys(port, PORT_KEYS, path)
    _required_keys(port, PORT_KEYS, path)
    _string(port["port_id"], f"{path}.port_id", minimum=1, maximum=256)
    _string(port["source_port_id"], f"{path}.source_port_id", maximum=128)
    _string(port["label"], f"{path}.label", maximum=500)
    _string(port["name"], f"{path}.name", maximum=128)
    _string(port["data_type"], f"{path}.data_type", maximum=128)
    _string(port["semantic_role"], f"{path}.semantic_role", maximum=256)
    _string(port["schema_ref"], f"{path}.schema_ref", maximum=1_000)
    if type(port["required"]) is not bool:
        raise ValueError(f"{path}.required must be a boolean")
    if port["cardinality"] not in {"one", "many"}:
        raise ValueError(f"{path}.cardinality is invalid")
    for field in ("has_default", "secret", "streaming"):
        if type(port[field]) is not bool:
            raise ValueError(f"{path}.{field} must be a boolean")
    _string(port["permission"], f"{path}.permission", maximum=500)
    _string(port["network_zone"], f"{path}.network_zone", maximum=128)


def _source_port(port: dict[str, Any]) -> dict[str, Any]:
    return {
        "port_id": port["source_port_id"],
        "name": port["name"],
        "data_type": port["data_type"],
        "semantic_role": port["semantic_role"],
        "schema_ref": port["schema_ref"],
        "cardinality": port["cardinality"],
        "required": port["required"],
        "has_default": port["has_default"],
        "secret": port["secret"],
        "permission": port["permission"],
        "network_zone": port["network_zone"],
        "streaming": port["streaming"],
    }


def _validate_skill(skill: Any, path: str) -> None:
    skill = _object(skill, path)
    _allowed_keys(skill, SKILL_KEYS, path)
    _required_keys(skill, SKILL_KEYS, path)
    _string(skill["skill_id"], f"{path}.skill_id", minimum=1, maximum=128)
    _string(skill["name"], f"{path}.name", minimum=1, maximum=256)
    _string(skill["version"], f"{path}.version", minimum=1, maximum=128)
    _string(skill["prompt_sha256"], f"{path}.prompt_sha256", maximum=71, pattern=SHA256_PATTERN)
    _string(skill["match_reason"], f"{path}.match_reason", minimum=1, maximum=5_000)
    _string(skill["target_stage"], f"{path}.target_stage", minimum=1, maximum=128)
    if skill["source_ref"] != "approved-skill-registry":
        raise ValueError(f"{path}.source_ref is invalid")


def _validate_detail(detail: Any, path: str) -> None:
    detail = _object(detail, path)
    _allowed_keys(detail, DETAIL_KEYS, path)
    _required_keys(detail, DETAIL_KEYS, path)
    _string(detail["title"], f"{path}.title", maximum=500)
    _string(detail["current_work"], f"{path}.current_work", maximum=20_000)
    _array(detail["problems"], f"{path}.problems", maximum=500)
    _string(detail["improvement"], f"{path}.improvement", maximum=20_000)
    _string(detail["reuse_decision_reason"], f"{path}.reuse_decision_reason", maximum=5_000)
    if detail["asset_ref"] is not None:
        asset_ref = _object(detail["asset_ref"], f"{path}.asset_ref")
        _allowed_keys(asset_ref, {"asset_id", "version"}, f"{path}.asset_ref")
        _required_keys(asset_ref, {"asset_id", "version"}, f"{path}.asset_ref")
        _string(asset_ref["asset_id"], f"{path}.asset_ref.asset_id", minimum=1, maximum=200)
        _string(asset_ref["version"], f"{path}.asset_ref.version", minimum=1, maximum=100)
    for port_group in ("inputs", "outputs"):
        for index, port in enumerate(_array(detail[port_group], f"{path}.{port_group}", maximum=500)):
            _validate_port(port, f"{path}.{port_group}[{index}]")
    _object(detail["config"], f"{path}.config")
    _array(detail["secrets_permissions"], f"{path}.secrets_permissions", maximum=500)
    _object(detail["failure_policy"], f"{path}.failure_policy")
    if detail["human_review"] is not None:
        _object(detail["human_review"], f"{path}.human_review")
    _array(detail["tests"], f"{path}.tests", maximum=500)
    for index, skill in enumerate(_array(detail["applied_skills"], f"{path}.applied_skills", maximum=100)):
        _validate_skill(skill, f"{path}.applied_skills[{index}]")


def _validate_request(request: Any, path: str) -> None:
    request = _object(request, path)
    _allowed_keys(request, REQUEST_KEYS, path)
    _required_keys(request, REQUEST_KEYS, path)
    request_id = _string(
        request["generation_request_id"],
        f"{path}.generation_request_id",
        maximum=24,
        pattern=GENERATION_REQUEST_ID_PATTERN,
    )
    _string(request["target_node_id"], f"{path}.target_node_id", minimum=1, maximum=128)
    if request["template_version"] != "ccp-base-2026-08-27.v1":
        raise ValueError(f"{path}.template_version is invalid")
    if request["prompt_pack"] not in PROMPT_PACKS:
        raise ValueError(f"{path}.prompt_pack is invalid")
    _string(request["component_filename"], f"{path}.component_filename", maximum=87, pattern=COMPONENT_FILENAME_PATTERN)
    _string(request["class_name"], f"{path}.class_name", maximum=110, pattern=COMPONENT_CLASS_PATTERN)
    _string(request["prompt_sha256"], f"{path}.prompt_sha256", maximum=71, pattern=SHA256_PATTERN)
    request_text = _string(request["request_text"], f"{path}.request_text", minimum=1, maximum=200_000)
    actual_hash = "sha256:" + hashlib.sha256(request_text.encode("utf-8")).hexdigest()
    if request["prompt_sha256"] != actual_hash:
        raise ValueError(f"{path}.prompt_sha256 does not match request_text")
    if request_id != "gen-" + actual_hash.removeprefix("sha256:")[:20]:
        raise ValueError(f"{path}.generation_request_id is not derived from prompt_sha256")


def _validate_closed_shape(view_model: dict[str, Any]) -> None:
    _allowed_keys(view_model, TOP_LEVEL_KEYS, "report_view_model")
    _required_keys(view_model, TOP_LEVEL_KEYS, "report_view_model")
    if view_model["schema_version"] != "report_view_model.v1":
        raise ValueError("report_view_model.schema_version is invalid")
    if view_model["renderer_version"] != RENDERER_VERSION:
        raise ValueError("report_view_model.renderer_version is invalid")
    _string(view_model["report_id"], "report_view_model.report_id", maximum=31, pattern=REPORT_ID_PATTERN)
    _string(view_model["title"], "report_view_model.title", minimum=1, maximum=500)
    summary = _object(view_model["summary"], "report_view_model.summary")
    _allowed_keys(summary, SUMMARY_KEYS, "report_view_model.summary")
    _required_keys(summary, SUMMARY_KEYS, "report_view_model.summary")
    _string(summary["work_definition_id"], "report_view_model.summary.work_definition_id", minimum=1, maximum=128)
    _integer(summary["work_definition_revision"], "report_view_model.summary.work_definition_revision")
    if summary["approval_status"] != "APPROVED":
        raise ValueError("report_view_model.summary.approval_status is invalid")
    _string(summary["approved_hash"], "report_view_model.summary.approved_hash", maximum=71, pattern=SHA256_PATTERN)
    _string(summary["blueprint_id"], "report_view_model.summary.blueprint_id", minimum=1, maximum=128)
    _string(summary["blueprint_sha256"], "report_view_model.summary.blueprint_sha256", maximum=71, pattern=SHA256_PATTERN)
    _string(summary["catalog_snapshot_id"], "report_view_model.summary.catalog_snapshot_id", minimum=1, maximum=128)
    _string(summary["pattern"], "report_view_model.summary.pattern", minimum=1, maximum=128)
    _string(summary["pattern_reason"], "report_view_model.summary.pattern_reason", maximum=5_000)
    if summary["build_readiness"] not in BUILD_READINESS:
        raise ValueError("report_view_model.summary.build_readiness is invalid")
    sections = _array(view_model["sections"], "report_view_model.sections", maximum=100)
    for index, section in enumerate(sections):
        path = f"report_view_model.sections[{index}]"
        section = _object(section, path)
        _allowed_keys(section, SECTION_KEYS, path)
        _required_keys(section, SECTION_KEYS, path)
        _string(section["section_id"], f"{path}.section_id", minimum=1, maximum=128)
        _string(section["title"], f"{path}.title", minimum=1, maximum=500)
        _array(section["items"], f"{path}.items", maximum=1_000)
    trace = _object(view_model["retrieval_trace"], "report_view_model.retrieval_trace")
    for field in ("tenant_id", "snapshot_id", "work_definition_id"):
        _string(
            trace.get(field),
            f"report_view_model.retrieval_trace.{field}",
            minimum=1,
            maximum=128,
            pattern=IDENTITY_PATTERN,
        )
    _integer(
        trace.get("work_definition_revision"),
        "report_view_model.retrieval_trace.work_definition_revision",
    )
    for field in ("approved_hash", "design_scope_sha256", "query_plan_sha256", "candidate_allowlist_sha256"):
        _string(
            trace.get(field),
            f"report_view_model.retrieval_trace.{field}",
            maximum=71,
            pattern=SHA256_PATTERN,
        )
    allowlist = _array(
        trace.get("candidate_allowlist"),
        "report_view_model.retrieval_trace.candidate_allowlist",
        maximum=50,
    )
    allowlist_projection: list[dict[str, str]] = []
    allowlist_bindings: dict[tuple[str, str, str, str], str] = {}
    seen_allowlist: set[tuple[str, str]] = set()
    for index, item in enumerate(allowlist):
        path = f"report_view_model.retrieval_trace.candidate_allowlist[{index}]"
        item = _object(item, path)
        _allowed_keys(item, {"asset_id", "version", "asset_type", "technical_contract_status", "port_contract_sha256"}, path)
        _required_keys(item, {"asset_id", "version", "asset_type", "technical_contract_status", "port_contract_sha256"}, path)
        asset_id = _string(item["asset_id"], f"{path}.asset_id", minimum=1, maximum=200)
        version = _string(item["version"], f"{path}.version", minimum=1, maximum=100)
        if item["asset_type"] not in {"component", "flow"} or item["technical_contract_status"] not in TECHNICAL_STATUSES - {None}:
            raise ValueError(f"{path} has an invalid asset type or technical status")
        port_contract_sha256 = _string(
            item["port_contract_sha256"],
            f"{path}.port_contract_sha256",
            maximum=71,
            pattern=SHA256_PATTERN,
        )
        identity = (asset_id, version)
        if identity in seen_allowlist:
            raise ValueError(f"{path} duplicates an asset identity")
        seen_allowlist.add(identity)
        allowlist_projection.append(dict(item))
        allowlist_bindings[(asset_id, version, item["asset_type"], item["technical_contract_status"])] = port_contract_sha256
    allowlist_material = json.dumps(
        allowlist_projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    expected_allowlist_hash = "sha256:" + hashlib.sha256(allowlist_material.encode("utf-8")).hexdigest()
    if trace["candidate_allowlist_sha256"] != expected_allowlist_hash:
        raise ValueError("report_view_model.retrieval_trace.candidate_allowlist_sha256 is invalid")
    trace_bindings = {
        "work_definition_id": summary["work_definition_id"],
        "work_definition_revision": summary["work_definition_revision"],
        "approved_hash": summary["approved_hash"],
        "snapshot_id": summary["catalog_snapshot_id"],
    }
    for field, expected in trace_bindings.items():
        if field not in trace or trace.get(field) != expected:
            raise ValueError(f"report_view_model.retrieval_trace.{field} does not match summary")
    _string(view_model["source_contract_hash"], "report_view_model.source_contract_hash", maximum=71, pattern=SHA256_PATTERN)
    for graph_name in ("as_is_graph", "to_be_graph"):
        graph_path = f"report_view_model.{graph_name}"
        graph = _object(view_model[graph_name], graph_path)
        _allowed_keys(graph, GRAPH_KEYS, graph_path)
        _required_keys(graph, {"graph_id", "graph_kind", "nodes", "edges", "details", "generation_requests", "text_fallback"}, graph_path)
        _string(graph["graph_id"], f"{graph_path}.graph_id", minimum=1, maximum=128)
        expected_kind = "as_is" if graph_name == "as_is_graph" else "to_be"
        if graph["graph_kind"] != expected_kind:
            raise ValueError(f"{graph_path}.graph_kind is invalid")
        if graph.get("build_readiness") is not None and graph.get("build_readiness") not in BUILD_READINESS:
            raise ValueError(f"{graph_path}.build_readiness is invalid")
        if graph_name == "to_be_graph" and graph.get("build_readiness") != summary["build_readiness"]:
            raise ValueError("report_view_model.to_be_graph.build_readiness does not match summary")
        if graph.get("layout_direction") is not None and graph.get("layout_direction") not in {"left_to_right", "top_to_bottom"}:
            raise ValueError(f"{graph_path}.layout_direction is invalid")
        for index, node in enumerate(_array(graph["nodes"], f"{graph_path}.nodes", maximum=2_000)):
            node_path = f"report_view_model.{graph_name}.nodes[{index}]"
            node = _object(node, node_path)
            _allowed_keys(node, NODE_KEYS, node_path)
            _required_keys(node, NODE_KEYS, node_path)
            _string(node["node_id"], f"{node_path}.node_id", minimum=1, maximum=128)
            _string(node["source_node_id"], f"{node_path}.source_node_id", maximum=128)
            if node["node_kind"] not in NODE_KINDS:
                raise ValueError(f"{node_path}.node_kind is invalid")
            _string(node["title"], f"{node_path}.title", maximum=500)
            _integer(node["sequence"], f"{node_path}.sequence")
            if node["implementation_source"] not in IMPLEMENTATION_SOURCES:
                raise ValueError(f"{node_path}.implementation_source is invalid")
            _string(node["implementation_label"], f"{node_path}.implementation_label", maximum=256)
            if node["technical_contract_status"] not in TECHNICAL_STATUSES:
                raise ValueError(f"{node_path}.technical_contract_status is invalid")
            if node["implementation_source"] in {"catalog_component", "catalog_flow"}:
                _string(
                    node["port_contract_sha256"],
                    f"{node_path}.port_contract_sha256",
                    maximum=71,
                    pattern=SHA256_PATTERN,
                )
            elif node["port_contract_sha256"] is not None:
                raise ValueError(f"{node_path}.port_contract_sha256 is only valid for catalog nodes")
            _string(node["summary"], f"{node_path}.summary", maximum=10_000)
            canonical_ports: dict[str, list[dict[str, Any]]] = {"inputs": [], "outputs": []}
            for port_group, contract_key in (("input_ports", "inputs"), ("output_ports", "outputs")):
                for port_index, port in enumerate(_array(node[port_group], f"{node_path}.{port_group}", maximum=500)):
                    _validate_port(port, f"{node_path}.{port_group}[{port_index}]")
                    canonical_ports[contract_key].append(_source_port(port))
            if (
                node["implementation_source"] in {"catalog_component", "catalog_flow"}
                and _canonical_hash(canonical_ports) != node["port_contract_sha256"]
            ):
                raise ValueError(f"{node_path} display ports do not match the sealed catalog port contract")
            for skill_index, skill in enumerate(_array(node["applied_skills"], f"{node_path}.applied_skills", maximum=100)):
                _validate_skill(skill, f"{node_path}.applied_skills[{skill_index}]")
            _string(node["detail_ref"], f"{node_path}.detail_ref", minimum=1, maximum=128)
            if node["generation_request_ref"] is not None:
                _string(node["generation_request_ref"], f"{node_path}.generation_request_ref", minimum=1, maximum=128)
        for index, edge in enumerate(_array(graph["edges"], f"{graph_path}.edges", maximum=5_000)):
            edge_path = f"{graph_path}.edges[{index}]"
            edge = _object(edge, edge_path)
            _allowed_keys(edge, EDGE_KEYS, edge_path)
            _required_keys(edge, EDGE_KEYS, edge_path)
            _string(edge["edge_id"], f"{edge_path}.edge_id", minimum=1, maximum=128)
            _string(edge["source_node_id"], f"{edge_path}.source_node_id", minimum=1, maximum=128)
            if edge["source_port_id"] is not None:
                _string(edge["source_port_id"], f"{edge_path}.source_port_id", minimum=1, maximum=256)
            _string(edge["target_node_id"], f"{edge_path}.target_node_id", minimum=1, maximum=128)
            if edge["target_port_id"] is not None:
                _string(edge["target_port_id"], f"{edge_path}.target_port_id", minimum=1, maximum=256)
            if edge["edge_kind"] not in EDGE_KINDS:
                raise ValueError(f"{edge_path}.edge_kind is invalid")
            label = _string(edge["label"], f"{edge_path}.label", maximum=500)
            if not label.strip():
                raise ValueError(f"{edge_path}.label must contain non-whitespace text")
            if edge["condition"] is not None:
                _string(edge["condition"], f"{edge_path}.condition", maximum=2_000)
            if type(edge["is_default"]) is not bool:
                raise ValueError(f"{edge_path}.is_default must be a boolean")
            if edge["connection_validation_status"] not in CONNECTION_STATUSES:
                raise ValueError(f"{edge_path}.connection_validation_status is invalid")
            _object(edge["mapping"], f"{edge_path}.mapping")
            _object(edge["retry_policy"], f"{edge_path}.retry_policy")
        if "groups" in graph:
            for group_index, group in enumerate(_array(graph["groups"], f"{graph_path}.groups", maximum=500)):
                _object(group, f"{graph_path}.groups[{group_index}]")
        details = _object(graph["details"], f"{graph_path}.details")
        for detail_id, detail in details.items():
            _validate_detail(detail, f"{graph_path}.details.{detail_id}")
        for index, node in enumerate(graph["nodes"]):
            detail = details.get(node["detail_ref"])
            if not isinstance(detail, dict) or detail.get("inputs") != node["input_ports"] or detail.get("outputs") != node["output_ports"]:
                raise ValueError(
                    f"report_view_model.{graph_name}.nodes[{index}] detail ports do not match the displayed node ports"
                )
        if graph_name == "to_be_graph":
            for index, node in enumerate(graph["nodes"]):
                detail = details[node["detail_ref"]]
                if node["implementation_source"] not in {"catalog_component", "catalog_flow"}:
                    continue
                asset_ref = detail.get("asset_ref") if isinstance(detail, dict) else None
                asset_type = "component" if node["implementation_source"] == "catalog_component" else "flow"
                binding = (
                    asset_ref.get("asset_id") if isinstance(asset_ref, dict) else None,
                    asset_ref.get("version") if isinstance(asset_ref, dict) else None,
                    asset_type,
                    node["technical_contract_status"],
                )
                if allowlist_bindings.get(binding) != node["port_contract_sha256"]:
                    raise ValueError(
                        f"report_view_model.to_be_graph.nodes[{index}] catalog asset/port binding is invalid"
                    )
        requests = _object(graph["generation_requests"], f"{graph_path}.generation_requests")
        for request_id, request in requests.items():
            _validate_request(request, f"{graph_path}.generation_requests.{request_id}")
        for fallback_index, fallback in enumerate(_array(graph["text_fallback"], f"{graph_path}.text_fallback", maximum=5_000)):
            _string(fallback, f"{graph_path}.text_fallback[{fallback_index}]", maximum=20_000)

def _secret_key(value: Any) -> bool:
    text = str(value or "").casefold()
    compact = re.sub(r"[^a-z0-9]", "", text)
    parts = {item for item in re.split(r"[^a-z0-9]+", text) if item}
    if compact == "secretspermissions":
        return False
    if ("token" in parts and parts & {"max", "limit", "budget", "count"}) or (
        "session" in parts and parts & {"timeout", "ttl"}
    ):
        return False
    if "token" in compact and any(marker in compact for marker in {"maxtoken", "tokenlimit", "tokenbudget", "tokencount"}):
        return False
    if "session" in compact and any(marker in compact for marker in {"sessiontimeout", "sessionttl"}):
        return False
    strong_markers = SECRET_KEY_TOKENS
    return compact in SECRET_KEY_TOKENS or bool(parts & {"token", "session", "pwd"}) or any(
        marker in compact for marker in strong_markers
    )


def _is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value == "[REDACTED]"


def _secret_material_kind(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(pattern.search(str(key).strip()) for pattern in SECRET_VALUE_PATTERNS):
                return "object_key"
            if _secret_key(key) and item not in (None, "", False) and not _is_redacted(item):
                return "secret_field_value"
            found = _secret_material_kind(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _secret_material_kind(item)
            if found:
                return found
    elif isinstance(value, str) and not _is_redacted(value) and any(pattern.search(value.strip()) for pattern in SECRET_VALUE_PATTERNS):
        return "string_value"
    return None


def _validate_graph(graph: Any, graph_name: str, max_nodes: int, max_edges: int) -> None:
    if not isinstance(graph, dict):
        raise ValueError(f"{graph_name} must be an object")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    details = graph.get("details")
    requests = graph.get("generation_requests")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(details, dict) or not isinstance(requests, dict):
        raise ValueError(f"{graph_name} is missing nodes, edges, details, or generation_requests")
    if len(nodes) > max_nodes or len(edges) > max_edges:
        raise ValueError(f"{graph_name} exceeds render limits")
    node_ids: set[str] = set()
    detail_refs: set[str] = set()
    port_ids: set[str] = set()
    input_ports_by_node: dict[str, set[str]] = {}
    output_ports_by_node: dict[str, set[str]] = {}
    custom_request_targets: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"{graph_name} node must be an object")
        node_id = _text(node.get("node_id"), 128)
        detail_ref = _text(node.get("detail_ref"), 128)
        if not node_id or node_id in node_ids:
            raise ValueError(f"{graph_name} has a missing or duplicate node id")
        if not detail_ref or detail_ref in detail_refs or detail_ref not in details:
            raise ValueError(f"{graph_name} has an invalid detail ref")
        if node.get("node_kind") not in NODE_KINDS or node.get("implementation_source") not in IMPLEMENTATION_SOURCES:
            raise ValueError(f"{graph_name} has an invalid node kind or implementation source")
        generation_ref = node.get("generation_request_ref")
        if node.get("implementation_source") == "new_standalone_component":
            if not generation_ref or generation_ref not in requests or generation_ref in custom_request_targets:
                raise ValueError(f"{graph_name} has an invalid generation request ref")
            custom_request_targets[generation_ref] = node_id
        elif generation_ref is not None:
            raise ValueError(f"{graph_name} non-custom node cannot reference a generation request")
        node_ids.add(node_id)
        detail_refs.add(detail_ref)
        input_port_ids: set[str] = set()
        output_port_ids: set[str] = set()
        for direction, ports, owned_ids in (
            ("input", list(node.get("input_ports") or []), input_port_ids),
            ("output", list(node.get("output_ports") or []), output_port_ids),
        ):
            for port in ports:
                port_id = _text(port.get("port_id") if isinstance(port, dict) else None, 256)
                if not port_id or port_id in port_ids or port_id in owned_ids:
                    raise ValueError(f"{graph_name} has a missing or duplicate {direction} port id")
                port_ids.add(port_id)
                owned_ids.add(port_id)
        input_ports_by_node[node_id] = input_port_ids
        output_ports_by_node[node_id] = output_port_ids
    edge_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError(f"{graph_name} edge must be an object")
        edge_id = _text(edge.get("edge_id"), 128)
        if not edge_id or edge_id in edge_ids:
            raise ValueError(f"{graph_name} has a missing or duplicate edge id")
        if edge.get("source_node_id") not in node_ids or edge.get("target_node_id") not in node_ids:
            raise ValueError(f"{graph_name} has a dangling edge")
        source_port_id = edge.get("source_port_id")
        target_port_id = edge.get("target_port_id")
        if source_port_id is not None and source_port_id not in output_ports_by_node.get(edge.get("source_node_id"), set()):
            raise ValueError(f"{graph_name} has a dangling or wrong-owner source port")
        if target_port_id is not None and target_port_id not in input_ports_by_node.get(edge.get("target_node_id"), set()):
            raise ValueError(f"{graph_name} has a dangling or wrong-owner target port")
        if edge.get("connection_validation_status") not in CONNECTION_STATUSES:
            raise ValueError(f"{graph_name} has an invalid connection validation status")
        if not _text(edge.get("label"), 500).strip():
            raise ValueError(f"{graph_name} edge label is required")
        edge_ids.add(edge_id)
    if set(requests) != set(custom_request_targets):
        raise ValueError(f"{graph_name} generation request registry must exactly match custom node refs")
    for request_id, target_node_id in custom_request_targets.items():
        request = requests[request_id]
        if request.get("generation_request_id") != request_id or request.get("target_node_id") != target_node_id:
            raise ValueError(f"{graph_name} generation request identity or target binding is invalid")


def _static_graph(graph: dict[str, Any], heading: str) -> str:
    items = []
    for node in graph.get("nodes", []):
        detail = graph.get("details", {}).get(node.get("detail_ref"), {})
        skills = node.get("applied_skills") or []
        content = (
            f"<li><strong>{html.escape(_text(node.get('title'), 500))}</strong> "
            f"<span>({html.escape(_text(node.get('implementation_label'), 128))})</span>"
            f"<p>{html.escape(_text(node.get('summary'), 10_000))}</p>"
            f"<pre>{html.escape(json.dumps(detail, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)[:50_000])}</pre>"
            f"<p>적용 Skill: {html.escape(', '.join(_text(item.get('name'), 256) for item in skills if isinstance(item, dict)) or '없음')}</p></li>"
        )
        items.append(content)
    edges = "".join(
        f"<li>{html.escape(_text(edge.get('source_node_id'),128))} → {html.escape(_text(edge.get('target_node_id'),128))}: {html.escape(_text(edge.get('label'),500))}</li>"
        for edge in graph.get("edges", [])
    )
    return f"<section><h3>{html.escape(heading)}</h3><ol>{''.join(items)}</ol><h4>연결</h4><ul>{edges}</ul></section>"


class ResponsiveReportRendererComponent(Component):
    display_name = "Responsive Business Flow Report Renderer"
    description = "Renders a safe self-contained node-and-edge report from a validated view model."
    icon = "PanelsTopLeft"
    name = "ResponsiveReportRenderer"

    inputs = [
        DataInput(name="report_view_model", display_name="Report View Model", required=True),
        StrInput(name="renderer_version", display_name="Renderer Version", value=RENDERER_VERSION, advanced=True),
        MultilineInput(name="allowed_hosts_json", display_name="Allowed Link Hosts JSON", value="[]", advanced=True),
        BoolInput(
            name="safe_failure_envelope",
            display_name="F30 오류를 결과로 반환",
            value=False,
            advanced=True,
            info="F30 Flow에서는 켜 둡니다. renderer 검증 오류를 Chat Output의 BLOCKED JSON으로 반환합니다.",
        ),
        IntInput(name="max_nodes", display_name="Maximum Nodes per Graph", value=500, advanced=True),
        IntInput(name="max_edges", display_name="Maximum Edges per Graph", value=1000, advanced=True),
        IntInput(name="max_html_bytes", display_name="Maximum HTML Bytes", value=10_000_000, advanced=True),
    ]
    outputs = [Output(name="render_result", display_name="Rendered Report", method="render_report")]

    def render_report(self) -> Data:
        if not bool(getattr(self, "safe_failure_envelope", False)):
            return self._render_report()
        upstream = _upstream_f30_failure(getattr(self, "report_view_model", None), stage="f30_renderer")
        if upstream is not None:
            self.status = f"Report rendering blocked: {upstream['error']['code']}"
            return Data(data=upstream)
        try:
            return self._render_report()
        except (TypeError, ValueError, json.JSONDecodeError):
            result = _f30_terminal_failure(
                stage="f30_renderer",
                code="F30_REPORT_RENDER_INVALID",
                message="보고서 화면용 데이터를 안전하게 렌더링하지 못했습니다. F20 설계 결과의 보고서 계약을 확인한 뒤 다시 실행하세요.",
            )
            self.status = f"Report rendering blocked: {result['error']['code']}"
            return Data(data=result)

    def _render_report(self) -> Data:
        view_model = _view_model(self.report_view_model)
        _ensure_json_value(view_model, "report_view_model")
        if _secret_material_kind(view_model):
            raise ValueError("REPORT_SECRET_MATERIAL_DETECTED: report_view_model contains unredacted secret material")
        _validate_closed_shape(view_model)
        if view_model.get("schema_version") != "report_view_model.v1":
            raise ValueError("unsupported report view model version")
        renderer_version = _text(getattr(self, "renderer_version", ""), 128)
        if renderer_version != RENDERER_VERSION:
            raise ValueError(f"renderer_version must be {RENDERER_VERSION}")
        if view_model.get("renderer_version") != renderer_version:
            raise ValueError("report view model renderer_version does not match the renderer contract")
        if view_model.get("report_id") != _expected_report_id(view_model):
            raise ValueError("report_id does not match the canonical render input")
        max_nodes = max(1, min(int(getattr(self, "max_nodes", 500) or 500), 2_000))
        max_edges = max(1, min(int(getattr(self, "max_edges", 1000) or 1000), 5_000))
        _validate_graph(view_model.get("as_is_graph"), "as_is_graph", max_nodes, max_edges)
        _validate_graph(view_model.get("to_be_graph"), "to_be_graph", max_nodes, max_edges)
        try:
            allowed_hosts_value = json.loads(str(getattr(self, "allowed_hosts_json", "[]") or "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("allowed_hosts_json must be a JSON array") from exc
        allowed_hosts = _normalize_allowed_hosts(allowed_hosts_value)
        summary = view_model.get("summary") if isinstance(view_model.get("summary"), dict) else {}
        summary_cards = "".join(
            f"<div class=\"summary-card\"><span>{html.escape(_text(key,128))}</span><strong>{html.escape(_text(value,2000))}</strong></div>"
            for key, value in sorted(summary.items(), key=lambda item: item[0])
        )
        support = "".join(
            f"<details><summary>{html.escape(_text(section.get('title'),500))}</summary><pre>{html.escape(json.dumps(section.get('items'),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)[:100_000])}</pre></details>"
            for section in view_model.get("sections", [])
            if isinstance(section, dict)
        )
        retrieval_trace = view_model.get("retrieval_trace") if isinstance(view_model.get("retrieval_trace"), dict) else {}
        if retrieval_trace:
            support += (
                "<details><summary>검색 근거와 snapshot trace</summary><pre>"
                + html.escape(json.dumps(retrieval_trace, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)[:100_000])
                + "</pre></details>"
            )
        static_fallback = _static_graph(view_model["as_is_graph"], "AS-IS 업무 Flow") + _static_graph(
            view_model["to_be_graph"], "TO-BE Agent Flow"
        )
        json_payload = _escaped_json(view_model)
        document = (
            "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta name=\"renderer-version\" content=\"{html.escape(renderer_version)}\">"
            f"<title>{html.escape(_text(view_model.get('title'), 500))}</title>"
            f"<style>{CSS}</style></head><body>"
            "<main class=\"shell\">"
            "<div class=\"topbar\">"
            "<div class=\"brand\"><div class=\"brand-mark\">A</div><span>업무 Agent 설계</span></div>"
            "<div class=\"meta\">업무 흐름 설계안</div>"
            "</div>"
            "<section class=\"intro\">"
            "<div class=\"intro-main\">"
            "<div class=\"eyebrow\">WORKFLOW REDESIGN</div>"
            "<h1 id=\"page-title\">업무 흐름을 더 단순하게,<br>Agent와 함께.</h1>"
            "<p id=\"page-desc\"></p>"
            "</div>"
            "<aside class=\"intro-side\">"
            "<div class=\"side-title\">설계 상태</div>"
            "<div class=\"status\"><span class=\"dot\"></span><span>설계안 준비 완료</span></div>"
            "<div class=\"quick-meta\">"
            "<span>승인 상태 <b id=\"approval-meta\">-</b></span>"
            "<span>업무 정의 <b id=\"revision-meta\">-</b></span>"
            "</div>"
            "<div class=\"side-title\" style=\"margin-top:20px\">왜 이렇게 설계했나요?</div>"
            "<div class=\"side-reason\" id=\"reason\"></div>"
            "</aside>"
            "</section>"
            "<div class=\"tabs\">"
            "<button class=\"tab\" data-tab=\"as_is\">현재 업무</button>"
            "<button class=\"tab active\" data-tab=\"to_be\">Agent 적용 후</button>"
            "</div>"
            "<section id=\"flow-panel\" class=\"flow-panel\">"
            "<div class=\"flow-head\">"
            "<div>"
            "<div class=\"flow-kicker\" id=\"flow-kicker\">CURRENT</div>"
            "<h2 id=\"flow-title\">현재 업무 흐름</h2>"
            "<p id=\"flow-desc\">지금 사람이 직접 수행하는 과정을 보여줍니다.</p>"
            "</div>"
            "<div class=\"legend\" id=\"legend\"></div>"
            "</div>"
            "<div id=\"graph-host\"></div>"
            "</section>"
            "<section class=\"support\"><h2>설계 참고 정보</h2><div id=\"support\"></div></section>"
            f"<section class=\"support static-fallback\"><h2>순서형 전체 내용</h2>{static_fallback}{support}</section>"
            "</main>"
            "<div id=\"backdrop\" class=\"drawer-backdrop\"></div>"
            "<aside id=\"drawer\" class=\"drawer\">"
            "<div class=\"drawer-head\"><div class=\"drawer-head-row\">"
            "<div><h2 id=\"drawer-title\">상세 정보</h2></div>"
            "<button id=\"close\" class=\"close\" type=\"button\" aria-label=\"상세 닫기\">×</button>"
            "</div></div>"
            "<div id=\"drawer-body\" class=\"drawer-body\"></div>"
            "</aside>"
            f"<script id=\"report-data\" type=\"application/json\">{json_payload}</script>"
            f"<script>{JS}</script></body></html>"
        )
        max_bytes = max(100_000, min(int(getattr(self, "max_html_bytes", 10_000_000) or 10_000_000), 15_000_000))
        byte_count = len(document.encode("utf-8"))
        if byte_count > max_bytes:
            raise ValueError("rendered HTML exceeds max_html_bytes")
        digest = _sha256_text(document)
        result = {
            "ok": True,
            "status": "RENDERED",
            "report_id": _text(view_model.get("report_id"), 128),
            "title": _text(view_model.get("title"), 500),
            "renderer_version": renderer_version,
            "html": document,
            "content_sha256": "sha256:" + digest,
            "script_csp_hash": _csp_hash(JS),
            "style_csp_hash": _csp_hash(CSS),
            "byte_count": byte_count,
            "allowed_hosts": allowed_hosts,
            "accessibility_summary": {
                "keyboard_node_selection": True,
                "focusable_edge_labels": True,
                "reduced_motion": True,
                "text_fallback": True,
                "print_expanded": True,
            },
        }
        self.status = f"Rendered report {result['report_id']} ({byte_count} bytes)"
        return Data(data=result)
