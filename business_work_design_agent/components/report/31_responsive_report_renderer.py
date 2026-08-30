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
from lfx.io import DataInput, IntInput, MultilineInput, Output, StrInput
from lfx.schema import Data


RENDERER_VERSION = "business-report-renderer.v1"
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
:root{color-scheme:light;--bg:#f4f6fb;--panel:#fff;--ink:#172033;--muted:#667085;--line:#cbd3e1;--focus:#6847e8;--violet:#7048e8;--teal:#0f9f8f;--blue:#3c67c7;--amber:#b7791f;--red:#c24152;--shadow:0 14px 40px rgba(36,45,74,.12);font-family:Inter,"Noto Sans KR","Segoe UI",sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink)}button{font:inherit}a{color:#304fd0}.report-shell{max-width:1680px;margin:0 auto;padding:24px}.hero{background:linear-gradient(135deg,#19172d,#302451 62%,#1e4265);color:white;border-radius:24px;padding:28px;box-shadow:var(--shadow)}.hero h1{margin:0 0 10px;font-size:clamp(1.65rem,3vw,2.6rem)}.hero p{margin:0;max-width:900px;color:#e5e1f7}.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:20px}.summary-card{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.16);border-radius:14px;padding:12px}.summary-card span{display:block;color:#d8d4e8;font-size:.76rem;text-transform:uppercase}.summary-card strong{display:block;margin-top:5px;overflow-wrap:anywhere}.report-section{background:var(--panel);border:1px solid #e2e7f0;border-radius:20px;margin-top:20px;padding:20px;box-shadow:0 6px 22px rgba(36,45,74,.06)}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.section-heading h2{margin:0;font-size:1.25rem}.legend{display:flex;gap:7px;flex-wrap:wrap}.badge{display:inline-flex;align-items:center;gap:4px;border:1px solid #d7ddeb;border-radius:999px;padding:3px 8px;font-size:.72rem;background:#f8f9fc;color:#3b455d}.badge.skill{background:#f2edff;border-color:#d8c9ff;color:#5830b7}.badge.new{background:#fff4e6;border-color:#ffd7a3;color:#9a5700}.graph-frame{position:relative;border:1px solid #dfe5ef;border-radius:16px;overflow:hidden;background-color:#fbfcff;background-image:radial-gradient(#cfd7e7 1px,transparent 1px);background-size:20px 20px;min-height:520px}.graph-toolbar{position:absolute;z-index:12;right:12px;top:12px;display:flex;align-items:center;gap:4px;background:#fff;border:1px solid #dfe5ef;border-radius:12px;padding:5px;box-shadow:0 6px 18px rgba(25,32,52,.1)}.graph-toolbar button{border:0;background:#f4f6fa;color:#263149;border-radius:8px;min-width:34px;height:34px;cursor:pointer}.graph-toolbar button:hover{background:#e9e6fb}.graph-toolbar button:focus-visible,.node-main:focus-visible,.edge-label:focus-visible,.drawer-close:focus-visible{outline:3px solid #b8a8ff;outline-offset:2px}.graph-viewport{position:absolute;inset:0;overflow:auto;cursor:grab;touch-action:pan-x pan-y}.graph-viewport.dragging{cursor:grabbing;user-select:none}.graph-world{position:relative;transform-origin:0 0;min-width:100%;min-height:100%}.edge-layer{position:absolute;inset:0;overflow:visible;pointer-events:none}.edge-path{fill:none;stroke:var(--blue);stroke-width:2.2}.edge-path.branch{stroke:var(--violet)}.edge-path.human{stroke:var(--amber)}.edge-path.retry{stroke:var(--amber);stroke-dasharray:7 6}.edge-path.error{stroke:var(--red);stroke-dasharray:6 5}.edge-path.selected{stroke-width:4;filter:drop-shadow(0 1px 2px rgba(73,55,151,.25))}.edge-hit{fill:none;stroke:transparent;stroke-width:16;pointer-events:stroke}.edge-label{position:absolute;z-index:5;max-width:170px;border:1px solid #d5dcec;background:#fff;border-radius:999px;padding:4px 9px;font-size:.72rem;color:#38435b;box-shadow:0 4px 12px rgba(36,45,74,.09);cursor:pointer}.edge-label.selected{border-color:var(--violet);background:#f3efff}.node-layer{position:absolute;inset:0}.flow-node{position:absolute;width:232px;min-height:126px;border:1px solid #d8deea;border-left:5px solid var(--blue);border-radius:16px;background:#fff;box-shadow:0 10px 25px rgba(38,50,80,.1);overflow:hidden}.flow-node.work_step{border-left-color:#56657d}.flow-node.decision{border-left-color:var(--violet);transform:rotate(0deg)}.flow-node.human_gate{border-left-color:var(--amber);background:#fffdf7}.flow-node.new_custom{border-style:dashed;border-left:5px dashed #e58225;background:#fffaf4}.flow-node.companion_service{border-left-color:#697386;background:#f9fafb}.flow-node.skill_group{border-left-color:var(--violet);background:#f9f6ff}.flow-node.exception{border-left-color:var(--red);background:#fff8f9}.flow-node.start,.flow-node.end{border-left-color:var(--teal);border-radius:999px;min-height:92px}.flow-node.selected{border-color:var(--focus);box-shadow:0 0 0 3px rgba(104,71,232,.18),0 14px 30px rgba(38,50,80,.16)}.flow-node.related{box-shadow:0 0 0 2px rgba(60,103,199,.18),0 10px 25px rgba(38,50,80,.1)}.node-main{display:block;width:100%;min-height:122px;text-align:left;border:0;background:transparent;color:inherit;padding:13px 14px;cursor:pointer}.node-eyebrow{display:flex;justify-content:space-between;gap:8px;align-items:center;color:var(--muted);font-size:.7rem;text-transform:uppercase}.node-main h3{margin:8px 0 5px;font-size:.96rem}.node-main p{margin:0;color:var(--muted);font-size:.78rem;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.node-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}.drawer{position:fixed;z-index:30;right:0;top:0;width:min(430px,40vw);height:100vh;background:#fff;border-left:1px solid #dfe4ed;box-shadow:-18px 0 48px rgba(28,36,60,.17);transform:translateX(105%);transition:transform .2s ease;overflow:auto}.drawer.open{transform:translateX(0)}.drawer-header{position:sticky;top:0;background:#fff;border-bottom:1px solid #e5e9f1;padding:17px;display:flex;justify-content:space-between;gap:12px;align-items:start;z-index:2}.drawer-header h2{font-size:1.13rem;margin:0}.drawer-close{border:0;background:#f1f3f7;border-radius:9px;width:36px;height:36px;cursor:pointer}.drawer-body{padding:17px}.detail-block{border-top:1px solid #e8ebf2;padding:13px 0}.detail-block:first-child{border-top:0}.detail-block h3{font-size:.8rem;text-transform:uppercase;color:#59647a;margin:0 0 7px}.detail-block pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f7fa;border-radius:10px;padding:10px;font:inherit;font-size:.8rem;margin:0}.static-fallback{min-width:0;overflow:hidden}.js .static-fallback{display:none}.static-fallback section,.static-fallback ol,.static-fallback li{min-width:0}.static-fallback ol{padding-left:22px}.static-fallback li{margin:.45rem 0}.static-fallback pre{max-width:100%;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:#f7f8fb;padding:12px;border-radius:10px}.support-sections details{border-top:1px solid #e7eaf1;padding:11px 0}.support-sections summary{cursor:pointer;font-weight:650}.support-sections pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f8fb;padding:12px;border-radius:10px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1279px){.drawer{width:min(520px,65vw)}}
@media(max-width:767px){.report-shell{padding:12px}.hero{border-radius:17px;padding:20px}.report-section{padding:13px;border-radius:15px}.graph-frame{min-height:430px}.drawer{top:auto;bottom:0;width:100%;height:min(72vh,680px);border-left:0;border-top:1px solid #dfe4ed;border-radius:20px 20px 0 0;transform:translateY(105%)}.drawer.open{transform:translateY(0)}.legend{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}.drawer{transition:none}}
@media print{body{background:#fff}.report-shell{max-width:none;padding:0}.hero{background:#fff;color:#000;border:1px solid #aaa;box-shadow:none}.hero p,.summary-card span{color:#333}.summary-card{border-color:#aaa}.graph-frame,.drawer{display:none!important}.static-fallback{display:block!important}.report-section{break-inside:avoid;box-shadow:none}}
"""


JS = r"""
(()=>{'use strict';
const byId=(id)=>document.getElementById(id);const dataNode=byId('report-data');if(!dataNode)return;
let vm;try{vm=JSON.parse(dataNode.textContent||'{}')}catch(_){return}
document.documentElement.classList.add('js');
const drawer=byId('detail-drawer'),drawerTitle=byId('drawer-title'),drawerBody=byId('drawer-body'),drawerClose=byId('drawer-close');let lastFocus=null;
const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node};
const addBlock=(title,value)=>{if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return;const block=el('section','detail-block');block.append(el('h3','',title));const pre=el('pre','',typeof value==='string'?value:JSON.stringify(value,null,2));block.append(pre);drawerBody.append(block)};
const openDrawer=(title,detail,trigger)=>{lastFocus=trigger||document.activeElement;drawerTitle.textContent=title||'상세 정보';drawerBody.replaceChildren();Object.entries(detail||{}).forEach(([k,v])=>addBlock(k,v));drawer.classList.add('open');drawer.setAttribute('aria-hidden','false');drawerClose.focus()};
const closeDrawer=()=>{drawer.classList.remove('open');drawer.setAttribute('aria-hidden','true');if(lastFocus&&lastFocus.focus)lastFocus.focus()};drawerClose.addEventListener('click',closeDrawer);document.addEventListener('keydown',(event)=>{if(event.key==='Escape'&&drawer.classList.contains('open'))closeDrawer()});
const positionsFor=(nodes)=>{const perLayer=new Map(),positions=new Map();nodes.forEach((node,index)=>{const layer=Math.max(0,Number(node.sequence||index+1)-1),row=perLayer.get(layer)||0;perLayer.set(layer,row+1);positions.set(node.node_id,{x:70+layer*310,y:92+row*190})});return positions};
const relatedIds=(graph,nodeId)=>{const ids=new Set([nodeId]);graph.edges.forEach(edge=>{if(edge.source_node_id===nodeId)ids.add(edge.target_node_id);if(edge.target_node_id===nodeId)ids.add(edge.source_node_id)});return ids};
function renderGraph(graph,host){const frame=el('div','graph-frame');const toolbar=el('div','graph-toolbar');toolbar.setAttribute('aria-label','확대 축소');const viewport=el('div','graph-viewport');const world=el('div','graph-world');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('edge-layer');const nodeLayer=el('div','node-layer');world.append(svg,nodeLayer);viewport.append(world);frame.append(toolbar,viewport);host.append(frame);
let scale=1;const positions=positionsFor(graph.nodes||[]),maxX=Math.max(700,...[...positions.values()].map(p=>p.x+300)),maxY=Math.max(480,...[...positions.values()].map(p=>p.y+190));world.style.width=maxX+'px';world.style.height=maxY+'px';svg.setAttribute('width',String(maxX));svg.setAttribute('height',String(maxY));svg.setAttribute('viewBox',`0 0 ${maxX} ${maxY}`);
const applyScale=()=>{world.style.transform=`scale(${scale})`;world.style.width=(maxX*scale)+'px';world.style.height=(maxY*scale)+'px'};const zoom=(delta)=>{scale=Math.max(.45,Math.min(1.8,scale+delta));applyScale()};[['+','확대',()=>zoom(.1)],['100%','100% 초기화',()=>{scale=1;applyScale()}],['−','축소',()=>zoom(-.1)],['⌗','전체 흐름 맞춤',()=>{scale=Math.max(.45,Math.min(1,(viewport.clientWidth-32)/maxX));applyScale();viewport.scrollTo({left:0,top:0})}]].forEach(([text,label,fn])=>{const button=el('button','',text);button.type='button';button.setAttribute('aria-label',label);button.addEventListener('click',fn);toolbar.append(button)});
const marker=document.createElementNS('http://www.w3.org/2000/svg','marker');marker.id='arrow-'+graph.graph_id;marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','6');marker.setAttribute('markerHeight','6');marker.setAttribute('orient','auto-start-reverse');const arrow=document.createElementNS('http://www.w3.org/2000/svg','path');arrow.setAttribute('d','M 0 0 L 10 5 L 0 10 z');arrow.setAttribute('fill','currentColor');marker.append(arrow);const defs=document.createElementNS('http://www.w3.org/2000/svg','defs');defs.append(marker);svg.append(defs);
const nodeEls=new Map(),edgeEls=[];const selectNode=(node,button)=>{const related=relatedIds(graph,node.node_id);nodeEls.forEach((article,id)=>{article.classList.toggle('selected',id===node.node_id);article.classList.toggle('related',id!==node.node_id&&related.has(id))});edgeEls.forEach(item=>{const active=item.edge.source_node_id===node.node_id||item.edge.target_node_id===node.node_id;item.path.classList.toggle('selected',active);item.label.classList.toggle('selected',active)});const detail={...(graph.details||{})[node.detail_ref],implementation_source:node.implementation_source,technical_contract_status:node.technical_contract_status,applied_skills:node.applied_skills};if(node.generation_request_ref)detail.generation_request=(graph.generation_requests||{})[node.generation_request_ref];openDrawer(node.title,detail,button)};
(graph.edges||[]).forEach(edge=>{const s=positions.get(edge.source_node_id),t=positions.get(edge.target_node_id);if(!s||!t)return;const sx=s.x+232,sy=s.y+63,tx=t.x,ty=t.y+63,mid=Math.round((sx+tx)/2),d=`M ${sx} ${sy} H ${mid} V ${ty} H ${tx}`;const hit=document.createElementNS('http://www.w3.org/2000/svg','path');hit.setAttribute('d',d);hit.classList.add('edge-hit');const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',d);path.classList.add('edge-path',edge.edge_kind||'data');path.setAttribute('marker-end',`url(#arrow-${graph.graph_id})`);svg.append(hit,path);const label=el('button','edge-label',edge.label||'다음 단계');label.type='button';label.style.left=(mid-45)+'px';label.style.top=(Math.min(sy,ty)+Math.abs(ty-sy)/2-13)+'px';label.addEventListener('click',()=>openDrawer(edge.label||'연결 상세',{source_node:edge.source_node_id,source_port:edge.source_port_id,target_node:edge.target_node_id,target_port:edge.target_port_id,mapping:edge.mapping,condition:edge.condition,is_default:edge.is_default,retry_policy:edge.retry_policy,connection_validation_status:edge.connection_validation_status},label));nodeLayer.append(label);edgeEls.push({edge,path,label})});
(graph.nodes||[]).forEach(node=>{const p=positions.get(node.node_id);const article=el('article','flow-node '+(node.node_kind||'work_step'));article.style.left=p.x+'px';article.style.top=p.y+'px';const button=el('button','node-main');button.type='button';button.setAttribute('aria-label',`${node.title}, ${node.implementation_label}`);const eyebrow=el('div','node-eyebrow');eyebrow.append(el('span','',node.node_kind),el('span','',`#${node.sequence}`));button.append(eyebrow,el('h3','',node.title),el('p','',node.summary||''));const badges=el('div','node-badges');badges.append(el('span','badge'+(node.implementation_source==='new_standalone_component'?' new':''),node.implementation_label));if((node.applied_skills||[]).length)badges.append(el('span','badge skill',`Skill ${node.applied_skills.length}개 적용`));if(node.technical_contract_status)badges.append(el('span','badge',node.technical_contract_status));button.append(badges);button.addEventListener('click',()=>selectNode(node,button));article.append(button);nodeLayer.append(article);nodeEls.set(node.node_id,article)});
let dragging=false,startX=0,startY=0,startLeft=0,startTop=0;viewport.addEventListener('pointerdown',event=>{if(event.target.closest('button'))return;dragging=true;viewport.classList.add('dragging');startX=event.clientX;startY=event.clientY;startLeft=viewport.scrollLeft;startTop=viewport.scrollTop;viewport.setPointerCapture(event.pointerId)});viewport.addEventListener('pointermove',event=>{if(!dragging)return;viewport.scrollLeft=startLeft-(event.clientX-startX);viewport.scrollTop=startTop-(event.clientY-startY)});viewport.addEventListener('pointerup',()=>{dragging=false;viewport.classList.remove('dragging')});viewport.addEventListener('wheel',event=>{if(!event.ctrlKey&&!event.metaKey)return;event.preventDefault();zoom(event.deltaY<0?.1:-.1)},{passive:false});applyScale()}
document.querySelectorAll('[data-graph-target]').forEach(host=>{const graph=host.dataset.graphTarget==='as_is'?vm.as_is_graph:vm.to_be_graph;renderGraph(graph||{nodes:[],edges:[],details:{}},host)});
})();
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
        IntInput(name="max_nodes", display_name="Maximum Nodes per Graph", value=500, advanced=True),
        IntInput(name="max_edges", display_name="Maximum Edges per Graph", value=1000, advanced=True),
        IntInput(name="max_html_bytes", display_name="Maximum HTML Bytes", value=10_000_000, advanced=True),
    ]
    outputs = [Output(name="render_result", display_name="Rendered Report", method="render_report")]

    def render_report(self) -> Data:
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
            f"<title>{html.escape(_text(view_model.get('title'),500))}</title><style>{CSS}</style></head><body>"
            "<main class=\"report-shell\"><header class=\"hero\">"
            f"<h1>{html.escape(_text(view_model.get('title'),500))}</h1>"
            "<p>확정된 업무 방식과 Agent 구현 설계를 노드·연결선으로 비교합니다. 노드와 연결 label을 선택하면 상세 계약을 확인할 수 있습니다.</p>"
            f"<div class=\"summary-grid\">{summary_cards}</div></header>"
            "<section class=\"report-section\"><div class=\"section-heading\"><h2>AS-IS 업무 Flow</h2><div class=\"legend\"><span class=\"badge\">Human</span><span class=\"badge\">현재 시스템</span></div></div><div data-graph-target=\"as_is\"></div></section>"
            "<section class=\"report-section\"><div class=\"section-heading\"><h2>TO-BE Agent Flow</h2><div class=\"legend\"><span class=\"badge\">기본 요소</span><span class=\"badge\">기존 자산</span><span class=\"badge new\">신규 Custom</span><span class=\"badge skill\">Skill</span></div></div><div data-graph-target=\"to_be\"></div></section>"
            f"<section class=\"report-section support-sections\"><h2>설계 근거와 검증</h2>{support}</section>"
            f"<section class=\"report-section static-fallback\"><h2>순서형 전체 내용</h2>{static_fallback}</section>"
            "</main><aside id=\"detail-drawer\" class=\"drawer\" aria-hidden=\"true\" aria-label=\"노드 및 연결 상세\"><div class=\"drawer-header\"><h2 id=\"drawer-title\">상세 정보</h2><button id=\"drawer-close\" class=\"drawer-close\" type=\"button\" aria-label=\"상세 닫기\">×</button></div><div id=\"drawer-body\" class=\"drawer-body\"></div></aside>"
            f"<script id=\"report-data\" type=\"application/json\">{json_payload}</script><script>{JS}</script></body></html>"
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
