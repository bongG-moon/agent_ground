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

# v7 keeps the self-contained renderer contract but gives the report a more
# useful executive reading order: what is changing, which catalog asset is
# used at each stage, then the interactive AS-IS/TO-BE graph.  These rules are
# appended instead of replacing the original compatibility styles so older
# saved report models retain their print/no-JS fallback.
CSS += r"""
:root{--navy:#0f1f38;--navy-soft:#162d4e;--blue:#2e7cf6;--sky:#eaf3ff;--orange:#f26935;--paper:#f5f7fb;--ink:#172033;--muted:#657084;--line:#e3e8f0;--green:#1c9a78;--amber:#db922e}
body{background:var(--paper);color:var(--ink)}.shell{max-width:1480px;padding:26px 30px 72px}.topbar{margin-bottom:16px;padding:0 2px 14px;border-bottom:1px solid #dce3ec}.brand-mark{background:var(--navy);border-radius:7px}.meta{font-weight:700;color:#7e8897}.intro{display:block;margin:0 0 18px;border:0;border-radius:22px;background:linear-gradient(135deg,var(--navy) 0%,#1d416d 72%,#24558c 100%);box-shadow:0 18px 44px rgba(12,31,57,.19);overflow:hidden;position:relative}.intro:after{content:"";position:absolute;right:-70px;top:-110px;width:360px;height:360px;border:1px solid rgba(255,255,255,.16);border-radius:50%;box-shadow:0 0 0 42px rgba(255,255,255,.035),0 0 0 86px rgba(255,255,255,.028)}.intro-main,.intro-side{position:relative;z-index:1;background:transparent;border:0;box-shadow:none}.intro-main{padding:32px 34px 24px;border:0}.intro-main .eyebrow{display:block;color:#8dc5ff;font-size:11px}.intro h1{font-size:31px;line-height:1.22;color:#fff;margin:8px 0 9px;letter-spacing:-.04em;max-width:850px}.intro-main p{font-size:14px;line-height:1.65;color:#d6e3f3;max-width:780px}.intro-side{padding:0 34px 24px;display:block}.intro-side>.side-title:first-child{display:none}.status{display:inline-flex;padding:8px 11px;border:1px solid rgba(255,255,255,.2);border-radius:999px;background:rgba(255,255,255,.08);color:#fff;font-size:12px}.dot{background:#58d0a5;box-shadow:0 0 0 3px rgba(88,208,165,.14)}.quick-meta{position:static;margin:14px 0 0}.quick-meta span{background:rgba(255,255,255,.11);border:1px solid rgba(255,255,255,.13);color:#d6e3f3;border-radius:8px}.quick-meta b{color:#fff}.intro-side .side-title[style]{padding:0;margin:19px 0 6px!important;color:#93c9ff;font-size:11px;text-transform:uppercase;letter-spacing:.1em}.side-reason{padding:0;max-width:980px;background:transparent;border:0;box-shadow:none;color:#fff;font-size:15px;line-height:1.58;font-weight:620}.report-overview{display:grid;grid-template-columns:minmax(300px,.9fr) minmax(380px,1.1fr);gap:14px;margin:0 0 16px}.overview-card,.overview-note{background:#fff;border:1px solid var(--line);border-radius:17px;padding:20px 22px;box-shadow:0 8px 24px rgba(15,31,56,.045)}.overview-card h2,.overview-note h2{font-size:15px;margin:0 0 12px;letter-spacing:-.025em}.overview-note{background:linear-gradient(135deg,#fff 0%,#f7fbff 100%)}.overview-note p{margin:0;color:#516076;font-size:13px;line-height:1.62}.metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:11px 12px;border-radius:11px;background:#f6f8fb;border:1px solid #edf0f5}.metric span{display:block;color:#778296;font-size:11px}.metric strong{display:block;margin-top:3px;color:var(--navy);font-size:21px;letter-spacing:-.04em}.tabs{position:sticky;top:0;z-index:12;display:flex;gap:4px;margin:0 0 14px;padding:7px;background:rgba(243,246,250,.94);border:1px solid #dfe5ed;border-radius:14px;width:100%;backdrop-filter:blur(12px)}.tab{padding:9px 14px;border-radius:9px;font-size:13px!important;font-weight:800!important}.tab.active{background:var(--navy)!important;color:#fff!important;border-bottom:0!important;box-shadow:0 4px 12px rgba(15,31,56,.16)!important}.tab[data-tab="to_be"].active{background:var(--orange)!important}.flow-panel{border-radius:19px;border:1px solid var(--line);box-shadow:0 8px 24px rgba(15,31,56,.045);overflow:hidden;margin-bottom:16px}.flow-head{padding:21px 24px 16px;align-items:center}.flow-head h2{font-size:20px;color:var(--navy)}.flow-head p{font-size:13px}.flow-kicker{display:block;color:var(--blue)}.legend{max-width:48%}.pill{border:1px solid #e5e9ef;background:#f6f8fb;color:#536077}.pill.new{background:#fff0e9;border-color:#ffd7c5;color:#d95524}.pill.skill{background:#f2efff;border-color:#e3dcff}.graph-frame{height:480px;background:linear-gradient(#fbfcfe,#f6f9fd);border-top:1px solid #e9edf3}.graph-viewport{background-image:radial-gradient(#dce5f0 1px,transparent 1px);background-size:18px 18px}.flow-node{width:228px;min-height:150px;border-radius:15px;border-color:#dde4ed;box-shadow:0 8px 20px rgba(15,31,56,.08)}.flow-node:before{left:14px;right:14px;background:#8795a9}.flow-node.decision:before{background:var(--blue)}.flow-node.exception:before{background:#d45757}.flow-node.new_custom:before{background:var(--orange)}.flow-node.human_gate:before{background:var(--amber)}.node-main{min-height:150px;padding:17px 16px 10px}.node-main h3{font-size:15px;margin:9px 0 6px;color:#202b3b}.node-main p{font-size:11.5px;color:#647186}.node-badges{margin-top:9px}.node-link{display:block;margin:0 16px 13px;max-width:calc(100% - 32px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#2467c8;font-size:11px;font-weight:800;text-decoration:none}.node-link:hover{text-decoration:underline}.edge-path{stroke:#9dabbc;stroke-width:2.2}.edge-path.edge-branch{stroke:var(--blue)}.edge-path.edge-error{stroke:#d45757;stroke-dasharray:5 4}.edge-path.edge-human{stroke:var(--amber);stroke-dasharray:4 3}.edge-label{z-index:5;min-width:58px;max-width:130px;width:auto!important;border-color:#dce4ee;border-radius:7px;padding:4px 7px;background:#fff;color:#506078;font-size:10px;font-weight:700;box-shadow:0 3px 10px rgba(15,31,56,.06)}.toolbar{border-color:#dce4ee;border-radius:10px;box-shadow:0 6px 18px rgba(15,31,56,.1)}.catalog-panel{margin:18px 0;border:1px solid var(--line);border-radius:19px;background:#fff;box-shadow:0 8px 24px rgba(15,31,56,.045);overflow:hidden}.catalog-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;padding:22px 24px 15px;border-bottom:1px solid #edf0f4}.catalog-head h2{margin:0;color:var(--navy);font-size:19px;letter-spacing:-.03em}.catalog-head p{margin:5px 0 0;color:#6c7788;font-size:12.5px;line-height:1.55}.catalog-count{font-size:12px;color:#516076;font-weight:800;white-space:nowrap}.catalog-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:16px 20px 20px}.catalog-card{border:1px solid #e2e7ee;border-radius:14px;padding:16px;background:#fff}.catalog-card.selected{border-color:#bfd5fa;background:linear-gradient(135deg,#fff,#f7fbff)}.catalog-card.candidate{background:#fbfcfd}.catalog-card-top{display:flex;justify-content:space-between;gap:9px;align-items:flex-start}.catalog-status{display:inline-flex;padding:4px 7px;border-radius:999px;background:#edf3ff;color:#2866bc;font-size:10px;font-weight:850}.catalog-status.candidate{background:#f1f3f6;color:#657084}.catalog-stage{margin-top:8px;font-size:11px;color:#69758a;font-weight:750}.catalog-card h3{font-size:15px;margin:5px 0 5px;color:#202b3b;line-height:1.35}.catalog-meta{font-size:11px;color:#6d7889;line-height:1.55}.catalog-description{font-size:12px;color:#566277;line-height:1.6;margin:10px 0}.catalog-reason{padding:9px 10px;border-radius:9px;background:#f5f8fc;color:#46546b;font-size:11.5px;line-height:1.55}.catalog-link{display:inline-flex;margin-top:11px;color:#2467c8;font-size:12px;font-weight:850;text-decoration:none}.catalog-link:hover{text-decoration:underline}.catalog-empty{padding:22px 24px;color:#667388;font-size:13px;line-height:1.6}.support{margin-top:18px}.support h2{font-size:15px;color:var(--navy);margin:0 0 10px}.support-list{grid-template-columns:repeat(3,minmax(0,1fr));padding:0}.support-item{min-height:100px}.support-item strong{font-size:12px;color:#2a3546}.support-item p{font-size:11.5px}.drawer{border-radius:18px}.drawer-head{padding:20px}.drawer h2{color:var(--navy)}.detail .value{color:#3d4a5f}.drawer-link{display:inline-flex;margin-top:8px;color:#2467c8;font-size:13px;font-weight:800;text-decoration:none}.drawer-link:hover{text-decoration:underline}@media(max-width:900px){.shell{padding:16px}.intro-main{padding:26px 22px 19px}.intro-side{padding:0 22px 22px}.intro h1{font-size:25px}.report-overview{grid-template-columns:1fr}.graph-frame{height:450px}.catalog-grid{grid-template-columns:1fr;padding:14px}.catalog-head{align-items:flex-start;flex-direction:column}.support-list{grid-template-columns:1fr}.legend{max-width:none}}
"""

# The narrative is intentionally a first-class part of the report rather than
# an accordion below the graph. It is rendered from the sealed
# ``sections[].business_report`` payload with DOM text nodes in JS, so user
# supplied wording never becomes executable markup.
CSS += r"""
.business-report{margin:0 0 18px;border:1px solid var(--line);border-radius:19px;background:#fff;box-shadow:0 8px 24px rgba(15,31,56,.045);overflow:hidden}.business-report-head{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:24px 26px 20px;background:linear-gradient(135deg,#f9fbff 0%,#edf5ff 100%);border-bottom:1px solid #dce7f5}.business-report-kicker{color:#2e72c9;font-size:10px;font-weight:900;letter-spacing:.12em;text-transform:uppercase}.business-report-head h2{margin:6px 0 7px;color:var(--navy);font-size:23px;letter-spacing:-.04em}.business-report-lead{max-width:900px;margin:0;color:#51627a;font-size:13px;line-height:1.68}.business-report-state{flex:0 0 auto;margin-top:3px;padding:7px 10px;border:1px solid #cfe0f8;border-radius:999px;background:#fff;color:#2a65b2;font-size:11px;font-weight:850;white-space:nowrap}.business-report-nav{display:flex;gap:6px;flex-wrap:wrap;padding:13px 22px 0;background:#fff}.business-report-nav a{padding:6px 8px;border-radius:7px;color:#52647d;font-size:11px;font-weight:750;text-decoration:none}.business-report-nav a:hover{background:#eef5ff;color:#1e65ba}.business-report-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:18px 22px 24px}.narrative-section{position:relative;min-width:0;padding:17px 17px 16px;border:1px solid #e4e9f0;border-radius:14px;background:#fff}.narrative-section.primary{grid-column:span 2;border-color:#bfd7fa;background:linear-gradient(135deg,#fff 0%,#f7fbff 100%)}.narrative-section h3{margin:0 0 8px;color:#1b2d49;font-size:15px;letter-spacing:-.025em}.narrative-section p{margin:0;color:#526177;font-size:12.5px;line-height:1.65}.narrative-section p+div,.narrative-section p+ul{margin-top:12px}.narrative-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-top:12px}.narrative-fact{padding:8px 9px;border-radius:9px;background:#f6f8fb;border:1px solid #edf0f4}.narrative-fact span{display:block;color:#748096;font-size:10px;font-weight:700}.narrative-fact strong{display:block;margin-top:3px;color:#273650;font-size:11.5px;line-height:1.45;font-weight:760;white-space:pre-wrap;overflow-wrap:anywhere}.narrative-list{margin:12px 0 0;padding:0;list-style:none}.narrative-list li{position:relative;margin:7px 0;padding-left:15px;color:#4e5d72;font-size:12px;line-height:1.58}.narrative-list li:before{content:"";position:absolute;left:1px;top:.61em;width:6px;height:6px;border-radius:50%;background:#4d89df}.narrative-section[data-key="improvement_direction"] li:before,.narrative-section[data-key="to_be_operating_plan"] li:before{background:var(--orange)}.narrative-section[data-key="implementation_allocation"] li:before,.narrative-section[data-key="next_steps"] li:before{background:var(--green)}.narrative-empty{padding:22px 24px;color:#667388;font-size:13px;line-height:1.65}.business-report-static{padding:20px 22px}.business-report-static h2{margin:0 0 10px;color:var(--navy);font-size:18px}.business-report-static h3{margin:16px 0 6px;color:#293955;font-size:14px}.business-report-static p,.business-report-static li{color:#526177;font-size:12px;line-height:1.6}.business-report-static pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fb;border-radius:9px;padding:10px;color:#4e5d72;font:11px/1.55 inherit}@media(max-width:900px){.business-report-head{padding:20px;flex-direction:column;gap:8px}.business-report-head h2{font-size:20px}.business-report-grid{grid-template-columns:1fr;padding:14px}.narrative-section.primary{grid-column:auto}.narrative-facts{grid-template-columns:1fr}.business-report-nav{padding:10px 14px 0}.business-report-state{margin-top:0}}
"""

# The static document is intentionally not inserted into the visual report
# shell.  When JavaScript is unavailable or a browser blocks the interaction
# layer, it remains available only as a collapsed technical reference at the
# end of the report.  This prevents a raw JSON dump from ever becoming the
# first thing a reader sees.
CSS += r"""
.technical-reference{margin-top:14px;border:1px solid #dde5ee;border-radius:12px;background:#fbfcfe}.technical-reference>summary{padding:13px 15px;color:#536177;font-size:12px;font-weight:800;cursor:pointer}.technical-reference[open]>summary{border-bottom:1px solid #e5ebf2}.technical-reference-body{padding:0 2px 14px}.technical-reference .business-report-static{padding:16px 18px}.technical-reference section{padding:0 18px}.technical-reference h3{color:#2b3b56;font-size:13px}.technical-reference h4{color:#536177;font-size:12px}.technical-reference pre{max-height:420px;overflow:auto}.technical-reference .support-list{display:block;padding:0 18px}.technical-reference .support-item{min-height:0;margin-top:8px}.technical-reference.static-fallback{display:block}.catalog-link-unavailable{display:inline-flex;margin-top:11px;color:#8b96a5;font-size:12px;font-weight:750}.node-id-note{display:block;margin-top:4px;color:#7e8998;font-size:10px;font-weight:650}@media print{.technical-reference{display:block!important;border:0}.technical-reference>summary{display:none}.technical-reference .technical-reference-body{display:block!important}.technical-reference .business-report-static{padding:0}.technical-reference pre{max-height:none;overflow:visible}}
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

# v7 interaction layer.  It deliberately builds cards and links with DOM
# APIs/textContent rather than interpolating catalog text into HTML, and lays
# out graph levels from edges so branch/merge paths remain readable.
JS = r"""
(()=>{'use strict';document.documentElement.classList.add('js');const vm=JSON.parse(document.getElementById('report-data').textContent);const $=selector=>document.querySelector(selector);const shell=$('#report-shell');let allowedHosts=[];try{allowedHosts=JSON.parse(shell?.dataset.allowedHosts||'[]')}catch(_){allowedHosts=[]}if(!Array.isArray(allowedHosts))allowedHosts=[];allowedHosts=allowedHosts.map(v=>String(v).toLowerCase());
const text=value=>value==null?'':String(value);const plural=(value,word)=>`${value}${word}`;const assetKey=(assetId,version)=>`${text(assetId)}::${text(version)}`;function safeUrl(value){const raw=text(value).trim();if(!raw||/[\x00-\x1f\x7f]/.test(raw)||!/^[a-z][a-z0-9+.-]*:/i.test(raw))return null;try{const url=new URL(raw);const host=url.hostname.toLowerCase();if(!/^https?:$/.test(url.protocol)||!host||url.username||url.password||(allowedHosts.length&&!allowedHosts.includes(host)))return null;url.hash='';return url.href}catch(_){return null}}
const sections=Array.isArray(vm.sections)?vm.sections:[];const catalogSection=sections.find(section=>section&&section.section_id==='catalog_recommendations');const catalogItems=Array.isArray(catalogSection?.items)?catalogSection.items.filter(item=>item&&typeof item==='object'):[];const catalogByKey=new Map(catalogItems.map(item=>[assetKey(item.asset_id,item.version),item]));
$('#page-title').textContent=vm.title||'업무 흐름을 더 단순하게, Agent와 함께.';$('#page-desc').textContent='현재 업무를 단계·분기·검토 지점으로 정리하고, 검증된 카탈로그 자산과 신규 구현이 맡을 역할을 함께 제안합니다.';$('#reason').textContent=vm.summary?.pattern_reason||'업무 요구와 승인 범위를 기준으로 반복 작업은 자동화하고 중요한 판단은 사람이 확인하도록 설계했습니다.';$('#approval-meta').textContent=vm.summary?.approval_status==='APPROVED'?'승인됨':(vm.summary?.approval_status||'-');$('#revision-meta').textContent='Rev. '+(vm.summary?.work_definition_revision??'-');
function metric(label,value){const item=document.createElement('div');item.className='metric';const key=document.createElement('span');key.textContent=label;const number=document.createElement('strong');number.textContent=text(value);item.append(key,number);return item}function renderOverview(){const graph=vm.to_be_graph||{};const nodes=Array.isArray(graph.nodes)?graph.nodes:[];const edges=Array.isArray(graph.edges)?graph.edges:[];const branchCount=edges.filter(edge=>edge&&(edge.edge_kind==='branch'||edge.edge_kind==='error'||edge.edge_kind==='retry'||edge.condition)).length;const selected=catalogItems.filter(item=>item.status==='selected_for_stage').length;const custom=nodes.filter(node=>node?.implementation_source==='new_standalone_component').length;const grid=$('#overview-metrics');grid.innerHTML='';[['TO-BE 업무 단계',nodes.length],['분기·예외 경로',branchCount],['카탈로그 적용',selected],['신규 Custom',custom]].forEach(([label,value])=>grid.append(metric(label,value)));const summary=$('#overview-text');if(selected){summary.textContent=`카탈로그에서 검증된 자산 ${plural(selected,'개')}를 업무 단계에 연결하고, 부족한 기능 ${plural(custom,'개')}는 Standalone Custom Component로 보완합니다.`}else if(catalogItems.length){summary.textContent=`카탈로그 후보 ${plural(catalogItems.length,'개')}를 검토했지만, 현재 설계에서는 직접 재사용보다 신규 구현 또는 기본 요소가 적합하다고 판단했습니다. 후보의 상세 계약은 아래에서 확인할 수 있습니다.`}else{summary.textContent='현재 승인 범위와 권한에서 직접 재사용 가능한 카탈로그 자산을 찾지 못해, 기본 요소·신규 Standalone Custom Component·사람 검토 단계를 조합합니다.'}}renderOverview();
const friendly={current_work:'현재는 이렇게 해요',problems:'불편한 점',improvement:'Agent 적용 후',failure_policy:'문제가 생기면',inputs:'받는 정보',outputs:'만드는 결과',human_review:'사람이 확인하는 부분',applied_skills:'사용하는 AI 기능',reuse_decision_reason:'이 방식을 선택한 이유',secrets_permissions:'필요 권한',tests:'확인할 항목',config:'설정',asset_ref:'연결된 기존 자산',implementation_source:'구현 방식',technical_contract_status:'연결 상태',generation_request:'개발 참고 정보',title:'단계 이름'};const hidden=new Set(['title','config','asset_ref','secrets_permissions','technical_contract_status','generation_request']);const fmt=value=>{if(typeof value==='string')return value;if(Array.isArray(value))return value.map(item=>typeof item==='string'?`• ${item}`:`• ${item?.description||item?.name||item?.label||JSON.stringify(item)}`).join('\n');if(value&&typeof value==='object'){if(!Object.keys(value).length)return '';if(value.error_code)return `오류가 발생하면 작업을 중단하고 원인을 표시합니다. (${value.error_code})`;return Object.entries(value).map(([key,item])=>`${key}: ${typeof item==='object'?JSON.stringify(item):item}`).join('\n')}return text(value)};
const drawer=$('#drawer'),backdrop=$('#backdrop');function addDetail(body,label,value){if(value==null||value===''||(Array.isArray(value)&&!value.length))return;const formatted=fmt(value);if(!formatted)return;const section=document.createElement('section');section.className='detail';const heading=document.createElement('h3');heading.textContent=label;const content=document.createElement('div');content.className='value';content.textContent=formatted;section.append(heading,content);body.append(section)}function addCatalogDetail(body,asset){if(!asset)return;const section=document.createElement('section');section.className='detail';const heading=document.createElement('h3');heading.textContent='카탈로그 자산';const title=document.createElement('div');title.className='value';title.textContent=`${asset.asset_title||asset.asset_id} · ${asset.asset_id} (${asset.version||'-'})`;section.append(heading,title);if(asset.description){const description=document.createElement('div');description.className='value';description.textContent=asset.description;section.append(description)}const url=safeUrl(asset.catalog_url);if(url){const link=document.createElement('a');link.className='drawer-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='카탈로그 상세 열기 ↗';section.append(link)}body.append(section)}function openDrawer(title,detail,asset){$('#drawer-title').textContent=title;const body=$('#drawer-body');body.innerHTML='';Object.entries(detail||{}).forEach(([key,value])=>{if(!hidden.has(key))addDetail(body,friendly[key]||key,value)});addCatalogDetail(body,asset);drawer.classList.add('open');backdrop.classList.add('open')}function close(){drawer.classList.remove('open');backdrop.classList.remove('open')}$('#close').onclick=close;backdrop.onclick=close;document.addEventListener('keydown',event=>{if(event.key==='Escape')close()});
const labels={Human:'사람이 수행','기본 요소':'기본 기능','신규 Custom':'신규 Custom','외부 서비스':'연결 서비스','기존 Component':'카탈로그 Component','기존 Flow':'카탈로그 Flow'};function catalogForNode(graph,node){const detail=graph?.details?.[node.detail_ref];const ref=detail?.asset_ref;return ref?catalogByKey.get(assetKey(ref.asset_id,ref.version)):null}function render(kind){const graph=kind==='as_is'?vm.as_is_graph:vm.to_be_graph;$('#flow-kicker').textContent=kind==='as_is'?'CURRENT WORKFLOW':'AGENT DESIGN';$('#flow-title').textContent=kind==='as_is'?'현재 업무 흐름':'Agent 적용 후 업무 흐름';$('#flow-desc').textContent=kind==='as_is'?'지금 사람이 직접 수행하는 절차와 판단 지점을 확인합니다.':'카탈로그 재사용, 신규 구현, 사람 검토가 어떤 순서와 분기로 연결되는지 확인합니다.';const legend=$('#legend');legend.innerHTML='';[...new Set((graph.nodes||[]).map(node=>node.implementation_label).filter(Boolean))].forEach(label=>{const pill=document.createElement('span');pill.className='pill'+(label==='신규 Custom'?' new':'');pill.textContent=labels[label]||label;legend.append(pill)});if((graph.nodes||[]).some(node=>(node.applied_skills||[]).length)){const pill=document.createElement('span');pill.className='pill skill';pill.textContent='AI Skill 적용';legend.append(pill)}const host=$('#graph-host');host.innerHTML='';renderGraph(graph||{},host)}
function graphLayout(graph){const nodes=Array.isArray(graph.nodes)?[...graph.nodes].sort((a,b)=>(a.sequence??0)-(b.sequence??0)||text(a.node_id).localeCompare(text(b.node_id))):[];const byId=new Map(nodes.map(node=>[node.node_id,node]));const incoming=new Map(nodes.map(node=>[node.node_id,[]]));const outgoing=new Map(nodes.map(node=>[node.node_id,[]]));(graph.edges||[]).forEach(edge=>{if(byId.has(edge?.source_node_id)&&byId.has(edge?.target_node_id)){outgoing.get(edge.source_node_id).push(edge);incoming.get(edge.target_node_id).push(edge)}});const rank=new Map(nodes.map(node=>[node.node_id,0]));const indegree=new Map(nodes.map(node=>[node.node_id,incoming.get(node.node_id).length]));const compare=(a,b)=>(a.sequence??0)-(b.sequence??0)||text(a.node_id).localeCompare(text(b.node_id));let ready=nodes.filter(node=>indegree.get(node.node_id)===0).sort(compare);const visited=new Set();while(ready.length){const node=ready.shift();if(visited.has(node.node_id))continue;visited.add(node.node_id);for(const edge of outgoing.get(node.node_id)||[]){const target=edge.target_node_id;rank.set(target,Math.max(rank.get(target)||0,(rank.get(node.node_id)||0)+1));indegree.set(target,(indegree.get(target)||1)-1);if(indegree.get(target)===0)ready.push(byId.get(target));ready.sort(compare)}}for(const node of nodes){if(!visited.has(node.node_id))rank.set(node.node_id,Math.max(rank.get(node.node_id)||0,Math.max(0,(node.sequence??1)-1)))}const levels=new Map();for(const node of nodes){const level=rank.get(node.node_id)||0;if(!levels.has(level))levels.set(level,[]);levels.get(level).push(node)}const lane=new Map();const sortedLevels=[...levels.keys()].sort((a,b)=>a-b);for(const level of sortedLevels){const entries=(levels.get(level)||[]).map(node=>{const parents=(incoming.get(node.node_id)||[]).map(edge=>lane.get(edge.source_node_id)).filter(value=>typeof value==='number');const desired=parents.length?parents.reduce((sum,value)=>sum+value,0)/parents.length:0;return{node,desired}}).sort((a,b)=>a.desired-b.desired||compare(a.node,b.node));let cursor=-Infinity;entries.forEach((entry,index)=>{let assigned=entries.length===1?entry.desired:Math.max(entry.desired,cursor===-Infinity?0:cursor+1.18);if(!Number.isFinite(assigned))assigned=index;lane.set(entry.node.node_id,assigned);cursor=assigned})}const minLane=Math.min(0,...[...lane.values()]);const pos=new Map();const W=228,H=150,gapX=146,gapY=96,left=54,top=78;let maxLane=0,maxRank=0;for(const node of nodes){const currentLane=(lane.get(node.node_id)||0)-minLane;const currentRank=rank.get(node.node_id)||0;maxLane=Math.max(maxLane,currentLane);maxRank=Math.max(maxRank,currentRank);pos.set(node.node_id,{x:left+currentRank*(W+gapX),y:top+currentLane*(H+gapY)})}return{nodes,byId,incoming,outgoing,pos,W,H,left,top,width:Math.max(430,left*2+maxRank*(W+gapX)+W),height:Math.max(360,top*2+maxLane*(H+gapY)+H)}}
function renderGraph(graph,host){const frame=document.createElement('div');frame.className='graph-frame';const viewport=document.createElement('div');viewport.className='graph-viewport';const world=document.createElement('div');world.className='graph-world';const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('edge-layer');const layer=document.createElement('div');layer.className='node-layer';world.append(svg,layer);viewport.append(world);frame.append(viewport);const toolbar=document.createElement('div');toolbar.className='toolbar';const minus=document.createElement('button'),read=document.createElement('div'),plus=document.createElement('button'),fit=document.createElement('button');minus.type=plus.type=fit.type='button';minus.textContent='−';plus.textContent='+';fit.textContent='↙';fit.title='전체 흐름 보기';read.className='zoom-readout';toolbar.append(minus,read,plus,fit);frame.append(toolbar);host.append(frame);const layout=graphLayout(graph);world.style.width=`${layout.width}px`;world.style.height=`${layout.height}px`;svg.setAttribute('width',layout.width);svg.setAttribute('height',layout.height);svg.setAttribute('viewBox',`0 0 ${layout.width} ${layout.height}`);let scale=1;function apply(){world.style.transform=`scale(${scale})`;read.textContent=`${Math.round(scale*100)}%`}function zoom(delta){scale=Math.max(.12,Math.min(1.6,+(scale+delta).toFixed(2)));apply()}function fitAll(){const horizontal=Math.max(.01,(viewport.clientWidth-48)/layout.width);const vertical=Math.max(.01,(viewport.clientHeight-48)/layout.height);scale=Math.min(1,horizontal,vertical);apply();viewport.scrollTo({left:0,top:0})}minus.onclick=()=>zoom(-.1);plus.onclick=()=>zoom(.1);fit.onclick=fitAll;const defs=document.createElementNS('http://www.w3.org/2000/svg','defs');const marker=document.createElementNS('http://www.w3.org/2000/svg','marker');marker.id=`arrow-${graph.graph_id||'flow'}`;[['viewBox','0 0 10 10'],['refX','9'],['refY','5'],['markerWidth','6'],['markerHeight','6'],['orient','auto']].forEach(([attribute,value])=>marker.setAttribute(attribute,value));const arrow=document.createElementNS('http://www.w3.org/2000/svg','path');arrow.setAttribute('d','M0 0 L10 5 L0 10z');arrow.setAttribute('fill','#9dabbc');marker.append(arrow);defs.append(marker);svg.append(defs);
(graph.edges||[]).forEach(edge=>{const source=layout.pos.get(edge?.source_node_id),target=layout.pos.get(edge?.target_node_id);if(!source||!target)return;const sx=source.x+layout.W,sy=source.y+layout.H/2,tx=target.x,ty=target.y+layout.H/2,mid=Math.max(sx+30,(sx+tx)/2);const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',`M${sx} ${sy} H${mid} V${ty} H${tx}`);path.classList.add('edge-path',`edge-${edge.edge_kind||'data'}`);path.setAttribute('marker-end',`url(#arrow-${graph.graph_id||'flow'})`);svg.append(path);const label=document.createElement('button');label.type='button';label.className='edge-label';label.textContent=edge.label||'다음 단계';if(edge.condition)label.title=`조건: ${edge.condition}`;label.style.left=`${mid}px`;label.style.top=`${sy===ty?sy-22:(sy+ty)/2}px`;label.onclick=()=>openDrawer(edge.label||'연결 정보',{'현재 단계':layout.byId.get(edge.source_node_id)?.title,'다음 단계':layout.byId.get(edge.target_node_id)?.title,'분기 조건':edge.condition||'','연결 유형':edge.edge_kind||'data','기본 경로':edge.is_default?'예':'아니오'});layer.append(label)});
layout.nodes.forEach(node=>{const position=layout.pos.get(node.node_id);const article=document.createElement('article');article.className=`flow-node ${node.node_kind||''}`;article.style.left=`${position.x}px`;article.style.top=`${position.y}px`;const button=document.createElement('button');button.type='button';button.className='node-main';const top=document.createElement('div');top.className='node-top';const stage=document.createElement('span');stage.textContent=node.node_kind==='decision'?'분기 판단':'업무 단계';const step=document.createElement('span');step.textContent=`STEP ${node.sequence}`;top.append(stage,step);const heading=document.createElement('h3');heading.textContent=node.title;const summary=document.createElement('p');summary.textContent=node.summary||graph.details?.[node.detail_ref]?.current_work||'';const badges=document.createElement('div');badges.className='node-badges';const type=document.createElement('span');type.className='pill'+(node.implementation_label==='신규 Custom'?' new':'');type.textContent=labels[node.implementation_label]||node.implementation_label||'구현 방식';badges.append(type);if((node.applied_skills||[]).length){const skill=document.createElement('span');skill.className='pill skill';skill.textContent='AI Skill';badges.append(skill)}button.append(top,heading,summary,badges);const asset=catalogForNode(graph,node);button.onclick=()=>openDrawer(node.title,{...(graph.details?.[node.detail_ref]||{}),implementation_source:labels[node.implementation_label]||node.implementation_label},asset);article.append(button);const url=safeUrl(asset?.catalog_url);if(url){const link=document.createElement('a');link.className='node-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=`카탈로그: ${asset.asset_title||asset.asset_id} ↗`;article.append(link)}layer.append(article)});requestAnimationFrame(fitAll);let dragging=false,startX=0,startY=0,scrollLeft=0,scrollTop=0;viewport.onpointerdown=event=>{if(event.target.closest('button,a'))return;dragging=true;startX=event.clientX;startY=event.clientY;scrollLeft=viewport.scrollLeft;scrollTop=viewport.scrollTop;viewport.setPointerCapture(event.pointerId)};viewport.onpointermove=event=>{if(dragging){viewport.scrollLeft=scrollLeft-(event.clientX-startX);viewport.scrollTop=scrollTop-(event.clientY-startY)}};viewport.onpointerup=()=>{dragging=false};viewport.addEventListener('wheel',event=>{if(event.ctrlKey||event.metaKey){event.preventDefault();zoom(event.deltaY<0?.1:-.1)}},{passive:false})}
function renderCatalog(){const count=$('#catalog-count');const host=$('#catalog-cards');host.innerHTML='';count.textContent=catalogItems.length?`${catalogItems.length}개 자산`:'직접 재사용 자산 없음';if(!catalogItems.length){const empty=document.createElement('div');empty.className='catalog-empty';const fallback=sections.find(section=>section?.section_id==='catalog_reuse')?.items?.[0];empty.textContent=fallback?.message||'카탈로그 검색 결과에서 현재 승인 범위에 직접 적용할 자산을 찾지 못했습니다. 신규 Custom Component가 필요한 경우에는 생성 요청 계약을 함께 확인하세요.';host.append(empty);return}catalogItems.forEach(asset=>{const card=document.createElement('article');const selected=asset.status==='selected_for_stage';card.className=`catalog-card ${selected?'selected':'candidate'}`;const top=document.createElement('div');top.className='catalog-card-top';const badge=document.createElement('span');badge.className=`catalog-status ${selected?'':'candidate'}`;badge.textContent=selected?'이 단계에 적용':'검토 후보';const type=document.createElement('span');type.className='catalog-meta';type.textContent=[asset.asset_type,asset.version,asset.technical_contract_status].filter(Boolean).join(' · ');top.append(badge,type);const stage=document.createElement('div');stage.className='catalog-stage';stage.textContent=`적용 위치 · ${asset.stage_title||'후보 검토'}`;const title=document.createElement('h3');title.textContent=asset.asset_title||asset.asset_id;const meta=document.createElement('div');meta.className='catalog-meta';meta.textContent=[asset.category,asset.asset_id].filter(Boolean).join(' · ');const description=document.createElement('p');description.className='catalog-description';description.textContent=asset.description||asset.stage_summary||'';const reason=document.createElement('div');reason.className='catalog-reason';reason.textContent=`선정/검토 이유 · ${asset.reuse_decision_reason||asset.stage_summary||'업무 요구와 카탈로그 계약을 기준으로 확인합니다.'}`;card.append(top,stage,title,meta);if(description.textContent)card.append(description);card.append(reason);const url=safeUrl(asset.catalog_url);if(url){const link=document.createElement('a');link.className='catalog-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='카탈로그 상세 열기 ↗';card.append(link)}else{const unavailable=document.createElement('span');unavailable.className='catalog-link-unavailable';unavailable.textContent='상세 링크 미등록';card.append(unavailable)}host.append(card)})}renderCatalog();
function renderSupport(){const host=$('#support');host.innerHTML='';sections.filter(section=>section&&section.section_id!=='catalog_recommendations'&&section.section_id!=='catalog_reuse').forEach(section=>{const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=section.title||'설계 참고 정보';details.append(summary);const list=document.createElement('div');list.className='support-list';const items=Array.isArray(section.items)?section.items:[];if(!items.length){const empty=document.createElement('div');empty.className='support-item';empty.textContent='현재 추가 확인 사항이 없습니다.';list.append(empty)}else{items.forEach(item=>{const row=document.createElement('div');row.className='support-item';const title=document.createElement('strong');const body=document.createElement('p');if(typeof item==='string'){title.textContent=item}else{title.textContent=item?.name||item?.title||item?.test_id||item?.description||'항목';body.textContent=item?.risk&&item?.control?`주의: ${item.risk} · 대응: ${item.control}`:(item?.description||item?.message||item?.control||'')}row.append(title);if(body.textContent)row.append(body);list.append(row)})}details.append(list);host.append(details)})}renderSupport();
$('.tabs').addEventListener('click',event=>{const button=event.target.closest('.tab');if(!button)return;document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab===button));render(button.dataset.tab)});render('to_be')})();
"""

# v8 narrative layer. It intentionally runs after the flow/candidate renderer
# above: the report narrative is the primary reading order, while the graph
# and catalog cards remain the evidence the reader can drill into.
JS += r"""
(()=>{'use strict';const vm=JSON.parse(document.getElementById('report-data').textContent);const sections=Array.isArray(vm.sections)?vm.sections:[];const businessSection=sections.find(section=>section&&section.section_id==='business_report');const record=value=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);const clean=(value,limit=1800)=>{if(typeof value!=='string'&&typeof value!=='number'&&typeof value!=='boolean')return'';return String(value).replace(/\s+/g,' ').trim().slice(0,limit)};const labelMap={goal:'업무 목표',trigger:'실행 시점',automation_intent:'자동화 의도',frequency_volume:'처리 주기·물량',sla:'완료 기준·SLA',scope_in:'포함 범위',scope_out:'제외 범위',actors:'담당 역할',systems:'연계 시스템',inputs:'입력 정보',outputs:'산출물',procedure:'현행 절차',pain_points:'주요 문제',decision_points:'판단 지점',exception_paths:'예외 처리',risks_controls:'위험·통제',objectives:'개선 목표',principles:'설계 원칙',recommended_procedure:'권장 절차',branch_and_exception_plan:'분기·예외 운영',human_review_points:'사람 검토 지점',catalog_reuse:'카탈로그 재사용',catalog_candidates:'카탈로그 검토 후보',new_standalone_components:'신규 Standalone Component',builtin_components:'Langflow 기본 요소',companion_services:'연계 서비스',human_tasks:'사람 업무',skills:'적용 Skill',next_steps:'다음 단계',validation_plan:'검증 계획',open_items:'추가 확인 사항',approval_basis:'승인 근거',build_readiness:'구현 준비 상태'};const friendly=key=>labelMap[key]||clean(key.replace(/_/g,' '),80);const firstText=(source,keys)=>{if(!record(source))return'';for(const key of keys){const value=clean(source[key]);if(value)return value}return''};function lineFromItem(value,depth=0){if(depth>2)return'';const scalar=clean(value,900);if(scalar)return scalar;if(Array.isArray(value)){return value.map(item=>lineFromItem(item,depth+1)).filter(Boolean).join(' · ')}if(!record(value))return'';const title=firstText(value,['title','name','label','stage','step','phase','role','system','asset_title','id']);const description=firstText(value,['summary','description','detail','action','value','reason','message','recommendation','current_work','improvement']);if(title&&description&&title!==description)return `${title} — ${description}`;if(title)return title;return Object.entries(value).map(([key,item])=>{const line=lineFromItem(item,depth+1);return line?`${friendly(key)}: ${line}`:''}).filter(Boolean).slice(0,4).join(' · ')}function unique(values,maximum=12){return [...new Set(values.map(value=>clean(value,1000)).filter(Boolean))].slice(0,maximum)}function normalizeBlock(value){const result={summary:'',facts:[],bullets:[]};const scalar=clean(value);if(scalar){result.summary=scalar;return result}if(Array.isArray(value)){result.bullets=unique(value.map(item=>lineFromItem(item)));return result}if(!record(value))return result;result.summary=firstText(value,['overview','summary','description','narrative','purpose','recommendation','message','goal']);for(const [key,item] of Object.entries(value)){if(['report_type','report_version','overview','summary','description','narrative','purpose','recommendation','message'].includes(key)||item==null||item==='')continue;if(typeof item==='string'||typeof item==='number'||typeof item==='boolean'){result.facts.push([friendly(key),clean(item,600)]);continue}if(Array.isArray(item)){const lines=unique(item.map(entry=>lineFromItem(entry)));for(const line of lines)result.bullets.push(`${friendly(key)} · ${line}`);continue}if(record(item)){const nestedSummary=firstText(item,['summary','description','overview','detail','message','value']);if(nestedSummary)result.bullets.push(`${friendly(key)} · ${nestedSummary}`);else{const nested=lineFromItem(item);if(nested)result.bullets.push(`${friendly(key)} · ${nested}`)}}}result.facts=result.facts.slice(0,8);result.bullets=unique(result.bullets);if(!result.summary&&result.facts.length)result.summary=result.facts[0][1];return result}function graphNodes(graph){return Array.isArray(graph?.nodes)?graph.nodes.filter(node=>node&&node.node_kind!=='start'&&node.node_kind!=='end'):[]}function detail(graph,node){return graph?.details?.[node?.detail_ref]||{}}function graphLines(graph,field){return unique(graphNodes(graph).map(node=>{const value=clean(detail(graph,node)[field])||clean(node.summary);return value?`${clean(node.title,180)} — ${value}`:clean(node.title,180)}),14)}function listSection(sectionId){const section=sections.find(item=>item&&item.section_id===sectionId);return Array.isArray(section?.items)?section.items:[]}const asIs=vm.as_is_graph||{};const toBe=vm.to_be_graph||{};const catalogSection=sections.find(section=>section&&section.section_id==='catalog_recommendations');const catalogItems=Array.isArray(catalogSection?.items)?catalogSection.items.filter(record):[];const selectedCatalog=catalogItems.filter(item=>item.status==='selected_for_stage');const customNodes=graphNodes(toBe).filter(node=>node.implementation_source==='new_standalone_component');const humanNodes=graphNodes(toBe).filter(node=>node.node_kind==='human_gate'||node.implementation_source==='human_task');const derived={executive_summary:{overview:clean(vm.title,500)||'승인된 업무 정의를 바탕으로 업무 절차와 Agent 구현안을 정리했습니다.',approval_basis:clean(vm.summary?.approval_status),build_readiness:clean(vm.summary?.build_readiness)},work_overview:{goal:'업무 목표는 승인된 업무 정의와 TO-BE Flow에 따라 자동화 범위와 사람 검토 범위를 분명히 하는 것입니다.',scope_in:graphNodes(asIs).map(node=>node.title),scope_out:['승인·권한·예외 판단은 사람 검토 없이 자동 실행하지 않습니다.']},operating_context:{inputs:graphNodes(asIs).map(node=>detail(asIs,node).inputs).flat().filter(Boolean).slice(0,10),outputs:graphNodes(toBe).map(node=>detail(toBe,node).outputs).flat().filter(Boolean).slice(0,10),actors:humanNodes.map(node=>node.title)},as_is_analysis:{procedure:graphLines(asIs,'current_work'),pain_points:unique(graphNodes(asIs).flatMap(node=>Array.isArray(detail(asIs,node).problems)?detail(asIs,node).problems:[]),14),decision_points:graphNodes(asIs).filter(node=>node.node_kind==='decision'||node.node_kind==='human_gate').map(node=>node.title)},improvement_direction:{summary:clean(vm.summary?.pattern_reason,1000)||'반복적인 조회·정리·초안 작성은 Agent가 수행하고, 중요한 승인과 예외 판단은 사람이 담당하도록 역할을 분리합니다.',objectives:graphLines(toBe,'improvement')},to_be_operating_plan:{recommended_procedure:graphLines(toBe,'improvement'),branch_and_exception_plan:(toBe.edges||[]).filter(edge=>edge&&(edge.edge_kind==='branch'||edge.edge_kind==='error'||edge.edge_kind==='retry'||edge.condition)).map(edge=>`${clean(edge.label,180)||'분기'}${edge.condition?` — 조건: ${clean(edge.condition,220)}`:''}`),human_review_points:humanNodes.map(node=>`${clean(node.title,180)} — ${clean(detail(toBe,node).human_review?.description||detail(toBe,node).failure_policy?.description||node.summary,600)}`)},implementation_allocation:{catalog_reuse:selectedCatalog.map(asset=>`${clean(asset.asset_title||asset.asset_id,200)} — ${clean(asset.stage_title||'TO-BE 단계',160)}${asset.reuse_decision_reason?`: ${clean(asset.reuse_decision_reason,500)}`:''}`),catalog_candidates:catalogItems.filter(asset=>asset.status!=='selected_for_stage').map(asset=>`${clean(asset.asset_title||asset.asset_id,200)} — ${clean(asset.technical_contract_status||'계약 확인 필요',120)}`),new_standalone_components:customNodes.map(node=>`${clean(node.title,180)} — ${clean(node.summary||detail(toBe,node).improvement,600)}`),builtin_components:graphNodes(toBe).filter(node=>node.implementation_source==='builtin').map(node=>node.title),human_tasks:humanNodes.map(node=>node.title),skills:graphNodes(toBe).flatMap(node=>Array.isArray(node.applied_skills)?node.applied_skills:[]).map(skill=>clean(skill?.name,180))},next_steps:['카탈로그 후보의 포트·권한·실행 계약을 확인합니다.','확정된 TO-BE Flow를 Langflow에서 연결하고 단계별 테스트를 수행합니다.','사람 승인·예외 경로를 포함한 운영 검증 후 배포 범위를 확정합니다.'],validation_plan:listSection('tests').map(item=>lineFromItem(item)),open_items:listSection('unresolved').map(item=>lineFromItem(item))};const businessReport=Array.isArray(businessSection?.items)?businessSection.items.find(item=>record(item)&&item.report_type==='business_report')||businessSection.items.find(record):null;const definitions=[['executive_summary','업무 개요',true],['work_overview','업무 범위와 목표',false],['operating_context','운영 대상과 입출력',false],['as_is_analysis','현행 절차 및 문제',true],['improvement_direction','개선 방향',true],['to_be_operating_plan','권장 운영 방식',true],['implementation_allocation','구현 분담 및 카탈로그 적용',true],['next_steps','구현 로드맵',false],['validation_plan','검증 기준',false],['open_items','추가 확인 사항',false]];function appendFactList(host,facts){if(!facts.length)return;const grid=document.createElement('div');grid.className='narrative-facts';facts.forEach(([label,value])=>{const item=document.createElement('div');item.className='narrative-fact';const key=document.createElement('span');key.textContent=label;const content=document.createElement('strong');content.textContent=value;item.append(key,content);grid.append(item)});host.append(grid)}function appendBulletList(host,bullets){if(!bullets.length)return;const list=document.createElement('ul');list.className='narrative-list';bullets.forEach(value=>{const item=document.createElement('li');item.textContent=value;list.append(item)});host.append(list)}function renderBusinessReport(){const host=document.getElementById('business-report');if(!host)return;host.replaceChildren();const head=document.createElement('header');head.className='business-report-head';const copy=document.createElement('div');const kicker=document.createElement('div');kicker.className='business-report-kicker';kicker.textContent='BUSINESS WORK DESIGN REPORT';const title=document.createElement('h2');title.id='business-report-title';title.textContent=clean(businessSection?.title,300)||'완성 업무 설계 보고서';const lead=document.createElement('p');lead.className='business-report-lead';lead.textContent=firstText(businessReport||{},['overview','summary'])||clean(vm.summary?.pattern_reason,1000)||'업무 정의부터 개선안, 운영 방식, 구현 분담까지 하나의 설계 보고서로 정리했습니다.';copy.append(kicker,title,lead);const state=document.createElement('div');state.className='business-report-state';state.textContent=businessReport?'업무 정의 기반':'Flow 기반 초안';head.append(copy,state);host.append(head);const nav=document.createElement('nav');nav.className='business-report-nav';nav.setAttribute('aria-label','보고서 목차');const grid=document.createElement('div');grid.className='business-report-grid';let count=0;definitions.forEach(([key,heading,primary])=>{const block=normalizeBlock(businessReport?.[key]??derived[key]);if(!block.summary&&!block.facts.length&&!block.bullets.length)return;count+=1;const id=`business-${key}`;const link=document.createElement('a');link.href=`#${id}`;link.textContent=heading;nav.append(link);const section=document.createElement('section');section.className=`narrative-section${primary?' primary':''}`;section.dataset.key=key;section.id=id;const headingNode=document.createElement('h3');headingNode.textContent=heading;section.append(headingNode);if(block.summary){const summary=document.createElement('p');summary.textContent=block.summary;section.append(summary)}appendFactList(section,block.facts);appendBulletList(section,block.bullets);grid.append(section)});if(!count){const empty=document.createElement('div');empty.className='narrative-empty';empty.textContent='완성 업무 설계 보고서 데이터가 아직 준비되지 않았습니다. 현재 Flow와 카탈로그 적용 계획을 기준으로 초안을 확인할 수 있습니다.';grid.append(empty)}host.append(nav,grid);if(businessSection){document.querySelectorAll('#support details').forEach(details=>{if(details.querySelector('summary')?.textContent===businessSection.title)details.remove()})}}renderBusinessReport()})();
"""

# v9 is a presentation-only compatibility pass for reports that still carry
# technical node identifiers (for example ``node-mail-ingest-sanitize``).
# The sealed model keeps those IDs for traceability, while the reader sees a
# concise Korean stage name and an operational description in the canvas.
JS += r"""
(()=>{'use strict';const vm=JSON.parse(document.getElementById('report-data').textContent);const $=selector=>document.querySelector(selector);const stageTitles={'node-trigger-start':'업무 실행 시작','node-mail-ingest-sanitize':'메일 수집·정제','node-starrocks-query-guard':'StarRocks 조회·품질 검증','node-draft-synthesizer':'주간보고 초안 생성','node-hitl-result-gate':'담당자 검토·승인','node-publish-and-notify':'승인본 게시·알림','node-pipeline-end':'업무 완료·이력 기록','node-failure-handler':'실패 처리·운영 알림'};const kindTitles={start:'업무 실행 시작',end:'업무 완료',work_step:'업무 처리',decision:'분기 판단',human_gate:'담당자 검토·승인',system_call:'시스템 연동',new_custom:'신규 자동화 처리',companion_service:'연계 서비스',skill_group:'AI Skill 처리',exception:'예외 처리'};const kindLabels={start:'업무 시작',end:'업무 완료',work_step:'업무 단계',decision:'분기 판단',human_gate:'사람 검토',system_call:'시스템 연동',new_custom:'신규 자동화',companion_service:'연계 서비스',skill_group:'AI Skill',exception:'예외 처리'};const clean=value=>typeof value==='string'?value.replace(/\s+/g,' ').trim():'';const isTechnical=value=>/^(?:node[-_])?[a-z0-9]+(?:[-_][a-z0-9]+)+$/i.test(clean(value));function activeGraph(){return document.querySelector('.tab.active')?.dataset.tab==='as_is'?vm.as_is_graph||{}:vm.to_be_graph||{}}function orderedNodes(graph){return(Array.isArray(graph.nodes)?[...graph.nodes]:[]).sort((left,right)=>(left.sequence??0)-(right.sequence??0)||String(left.node_id||'').localeCompare(String(right.node_id||'')))}function displayTitle(node){const title=clean(node?.title);if(/[가-힣]/.test(title)&&!isTechnical(title))return title;return stageTitles[node?.node_id]||kindTitles[node?.node_kind]||title||'업무 처리'}function displaySummary(graph,node){const detail=graph?.details?.[node?.detail_ref]||{};const preferred=graph?.graph_kind==='as_is'?clean(detail.current_work):clean(detail.improvement);return preferred||clean(node?.summary)||'세부 운영 내용을 확인하고 다음 단계로 결과를 전달합니다.'}function addOriginalId(button,node){if(!button||button.dataset.koreanized==='true')return;button.dataset.koreanized='true';button.addEventListener('click',()=>{const drawerTitle=$('#drawer-title');if(drawerTitle)drawerTitle.textContent=displayTitle(node);const body=$('#drawer-body');if(!body||body.querySelector('.original-node-id'))return;const note=document.createElement('div');note.className='node-id-note original-node-id';note.textContent=`원본 단계 ID: ${node.node_id||'-'}`;body.append(note)})}function localizeCanvas(){const graph=activeGraph();const nodes=orderedNodes(graph);document.querySelectorAll('#graph-host .flow-node').forEach((element,index)=>{const node=nodes[index];if(!node)return;const button=element.querySelector('.node-main');const spans=button?.querySelectorAll('.node-top span')||[];if(spans[0])spans[0].textContent=kindLabels[node.node_kind]||'업무 단계';if(spans[1])spans[1].textContent=`${node.sequence??index+1}단계`;const heading=element.querySelector('h3');if(heading)heading.textContent=displayTitle(node);const summary=element.querySelector('p');if(summary)summary.textContent=displaySummary(graph,node);addOriginalId(button,node)});const business=$('#business-report');if(business)business.setAttribute('aria-busy','false')}function localizeNarrative(){const labels={title:'보고서 제목'};const values={design_only:'설계 초안',proposed_unverified:'사전 검증 필요',import_ready:'가져오기 준비 완료'};document.querySelectorAll('.narrative-fact span').forEach(element=>{if(labels[element.textContent])element.textContent=labels[element.textContent]});document.querySelectorAll('.narrative-fact strong').forEach(element=>{if(values[element.textContent])element.textContent=values[element.textContent]})}function fitVisibleGraph(){const fit=$('#graph-host .toolbar button[title="전체 흐름 보기"]');if(fit)fit.click()}function applyCanvasPresentation(){requestAnimationFrame(()=>{localizeCanvas();localizeNarrative();fitVisibleGraph()})}applyCanvasPresentation();$('.tabs')?.addEventListener('click',()=>{requestAnimationFrame(applyCanvasPresentation)})})();
"""

# v10 removes implementation-shaped narrative values from the reader-facing
# summary.  The sealed view model remains untouched; exact source data stays
# available only in the collapsed technical reference below the report.
JS += r"""
(()=>{'use strict';const statusValues={design_only:'설계 초안',proposed_unverified:'사전 검증 필요',import_ready:'가져오기 준비 완료'};const fieldLabels={estimated_messages_per_run:'예상 처리 건수',frequency:'실행 주기',approval_due:'승인 마감',draft_due:'초안 마감',target_date:'목표 일자',timeout_hours:'대기 시간',max_items:'최대 처리 건수'};const atom=value=>{if(value==='weekly')return'매주';if(value==='daily')return'매일';if(value==='monthly')return'매월';if(typeof value==='number')return Number.isFinite(value)?String(value):'';if(typeof value==='boolean')return value?'예':'아니오';if(typeof value==='string')return statusValues[value]||value;if(Array.isArray(value))return value.map(atom).filter(Boolean).join(', ');if(value&&typeof value==='object')return Object.entries(value).map(([key,item])=>line(key,item)).filter(Boolean).join(' · ');return''};const line=(key,value)=>{let visible=atom(value);if(key==='estimated_messages_per_run'&&typeof value==='number')visible=`${value}건`;return visible?`${fieldLabels[key]||'상세 조건'}: ${visible}`:''};const normalizeFacts=()=>document.querySelectorAll('.narrative-fact').forEach(card=>{const label=card.querySelector('span');const value=card.querySelector('strong');if(!label||!value)return;if(label.textContent.trim()==='title'||label.textContent.trim()==='보고서 제목'){card.remove();return}const source=value.textContent.trim();if(statusValues[source]){value.textContent=statusValues[source];return}if(!source.startsWith('{')&&!source.startsWith('['))return;try{const rendered=atom(JSON.parse(source));if(rendered)value.textContent=rendered}catch(_){}});requestAnimationFrame(normalizeFacts)})();
"""


# v11 is the single reader-facing presentation contract.  Earlier renderer
# experiments are intentionally left above for source compatibility during
# this transition, but the document uses only the following CSS and JS values.
# Keeping one rendering pass prevents the canvas, narration, and catalog cards
# from being restyled or rewritten after the reader first sees them.
CSS = r"""
:root{--ink:#172033;--muted:#617087;--line:#dfe6ef;--paper:#f3f6fa;--card:#fff;--navy:#102746;--navy-2:#1e4f84;--blue:#2c77d7;--blue-soft:#edf5ff;--orange:#ea6937;--orange-soft:#fff1ea;--green:#168565;--green-soft:#eaf8f3;--amber:#b86f12;--amber-soft:#fff7e9;--red:#c74d55;--shadow:0 12px 32px rgba(17,43,75,.075)}
*{box-sizing:border-box}html{scrollbar-color:#c7d2e1 transparent;scrollbar-width:thin}body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Noto Sans KR","Segoe UI",sans-serif;letter-spacing:-.018em}.shell{max-width:1480px;margin:0 auto;padding:24px 30px 72px}.topbar{display:flex;align-items:center;justify-content:space-between;padding:0 2px 14px;margin-bottom:16px;border-bottom:1px solid #dce4ee}.brand{display:flex;align-items:center;gap:8px;color:var(--navy);font-size:14px;font-weight:850}.brand-mark{display:grid;width:24px;height:24px;place-items:center;border-radius:7px;background:var(--navy);color:#fff;font-size:11px;font-weight:900}.meta{color:#7d899a;font-size:12px;font-weight:700}
.intro{position:relative;overflow:hidden;margin:0 0 18px;border-radius:22px;background:linear-gradient(125deg,var(--navy) 0%,#183c68 63%,#2b65a3 100%);box-shadow:0 18px 42px rgba(13,39,73,.18)}.intro:after{position:absolute;right:-88px;top:-120px;width:360px;height:360px;border:1px solid rgba(255,255,255,.16);border-radius:50%;box-shadow:0 0 0 42px rgba(255,255,255,.04),0 0 0 86px rgba(255,255,255,.028);content:""}.intro-main,.intro-side{position:relative;z-index:1;background:transparent;border:0}.intro-main{padding:31px 34px 21px}.intro-main .eyebrow{margin:0;color:#90c7ff;font-size:10px;font-weight:900;letter-spacing:.13em}.intro h1{max-width:900px;margin:8px 0;color:#fff;font-size:31px;line-height:1.22;letter-spacing:-.045em}.intro-main p{max-width:800px;margin:0;color:#d5e3f3;font-size:14px;line-height:1.65}.intro-side{padding:0 34px 25px}.intro-side>.side-title:first-child{display:none}.status{display:inline-flex;align-items:center;gap:8px;padding:8px 11px;border:1px solid rgba(255,255,255,.22);border-radius:999px;background:rgba(255,255,255,.09);color:#fff;font-size:12px;font-weight:800}.dot{width:8px;height:8px;border-radius:50%;background:#60d2a9;box-shadow:0 0 0 3px rgba(96,210,169,.14)}.quick-meta{display:flex;flex-wrap:wrap;gap:7px;margin:13px 0 0}.quick-meta span{padding:6px 8px;border:1px solid rgba(255,255,255,.14);border-radius:8px;background:rgba(255,255,255,.1);color:#d5e3f3;font-size:11px}.quick-meta b{margin-left:4px;color:#fff}.intro-side .side-title[style]{margin:18px 0 6px!important;padding:0;color:#97caff;font-size:10px;font-weight:900;letter-spacing:.11em;text-transform:uppercase}.side-reason{max-width:980px;padding:0;background:transparent;border:0;box-shadow:none;color:#fff;font-size:15px;font-weight:650;line-height:1.58}
.report-overview{display:grid;grid-template-columns:minmax(280px,.82fr) minmax(380px,1.18fr);gap:14px;margin:0 0 16px}.overview-card,.overview-note{padding:20px 22px;border:1px solid var(--line);border-radius:17px;background:var(--card);box-shadow:var(--shadow)}.overview-card h2,.overview-note h2{margin:0 0 12px;color:var(--navy);font-size:15px;letter-spacing:-.025em}.overview-note{background:linear-gradient(135deg,#fff 0%,#f5faff 100%)}.overview-note p{margin:0;color:#4d5f78;font-size:13px;line-height:1.68}.metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:11px 12px;border:1px solid #edf1f6;border-radius:11px;background:#f7f9fc}.metric span{display:block;color:#78869a;font-size:10px;font-weight:750}.metric strong{display:block;margin-top:3px;color:var(--navy);font-size:21px;letter-spacing:-.04em}
.tabs{position:sticky;top:0;z-index:12;display:flex;gap:5px;width:100%;margin:0 0 14px;padding:7px;border:1px solid #dbe4ee;border-radius:14px;background:rgba(244,247,251,.94);backdrop-filter:blur(12px)}.tab{border:0;border-radius:9px;background:transparent;padding:9px 14px;color:#5c6a80;font-size:13px;font-weight:820;cursor:pointer}.tab:hover{background:#e8f0fa}.tab.active{background:var(--navy);box-shadow:0 4px 12px rgba(15,38,70,.16);color:#fff}.tab[data-tab="to_be"].active{background:var(--orange)}
.flow-panel,.catalog-panel,.business-report{overflow:hidden;margin:0 0 17px;border:1px solid var(--line);border-radius:19px;background:var(--card);box-shadow:var(--shadow)}.flow-head{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:21px 24px 16px}.flow-kicker{margin:0;color:var(--blue);font-size:10px;font-weight:900;letter-spacing:.12em}.flow-head h2{margin:5px 0 4px;color:var(--navy);font-size:20px;letter-spacing:-.035em}.flow-head p{margin:0;color:var(--muted);font-size:13px;line-height:1.55}.legend{display:flex;max-width:48%;flex-wrap:wrap;justify-content:flex-end;gap:6px}.pill{display:inline-flex;align-items:center;padding:5px 8px;border:1px solid #e3e9f0;border-radius:999px;background:#f5f8fb;color:#536176;font-size:10px;font-weight:800}.pill.new{border-color:#ffd6c5;background:var(--orange-soft);color:#c95022}.pill.skill{border-color:#ded8fe;background:#f2efff;color:#6856c6}
.graph-frame{position:relative;height:500px;border-top:1px solid #e7edf4;background:linear-gradient(#fcfdff,#f5f8fc);overflow:hidden}.graph-viewport{position:absolute;inset:0;overflow:auto;cursor:grab;background-image:radial-gradient(#d7e2ef 1px,transparent 1px);background-size:18px 18px;scrollbar-color:#becbdd transparent;scrollbar-width:thin}.graph-world{position:relative;transform-origin:0 0}.edge-layer,.node-layer{position:absolute;inset:0}.edge-layer{overflow:visible;pointer-events:none}.edge-path{fill:none;stroke:#96a7ba;stroke-width:2.2}.edge-path.edge-branch{stroke:var(--blue)}.edge-path.edge-human{stroke:var(--amber);stroke-dasharray:4 3}.edge-path.edge-error{stroke:var(--red);stroke-dasharray:6 4}.edge-label{position:absolute;z-index:4;max-width:145px;border:1px solid #dbe4ee;border-radius:8px;background:#fff;padding:4px 7px;color:#53647a;font-size:10px;font-weight:750;line-height:1.25;box-shadow:0 3px 10px rgba(15,38,70,.06);cursor:pointer;transform:translate(-50%,-50%)}.flow-node{position:absolute;width:244px;min-height:162px;border:1px solid #dce5ef;border-radius:15px;background:#fff;box-shadow:0 8px 20px rgba(15,38,70,.075);overflow:hidden}.flow-node:before{position:absolute;top:0;right:14px;left:14px;height:4px;border-radius:0 0 4px 4px;background:#8393a8;content:""}.flow-node.start:before,.flow-node.end:before{background:var(--green)}.flow-node.decision:before{background:var(--blue)}.flow-node.human_gate:before{background:var(--amber)}.flow-node.new_custom:before{background:var(--orange)}.flow-node.exception:before{background:var(--red)}.node-main{display:block;width:100%;min-height:143px;border:0;background:transparent;padding:17px 16px 10px;color:inherit;text-align:left;cursor:pointer}.node-main:hover{background:#fbfdff}.node-top{display:flex;justify-content:space-between;gap:8px;color:#7c8a9d;font-size:10px;font-weight:750}.node-main h3{margin:9px 0 6px;color:#202d40;font-size:15px;line-height:1.35}.node-main p{display:-webkit-box;margin:0;overflow:hidden;color:#637187;font-size:11.5px;line-height:1.5;-webkit-box-orient:vertical;-webkit-line-clamp:2}.node-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}.node-link{display:block;max-width:calc(100% - 32px);margin:0 16px 12px;overflow:hidden;color:#266dcc;font-size:11px;font-weight:800;text-decoration:none;text-overflow:ellipsis;white-space:nowrap}.node-link:hover{text-decoration:underline}.toolbar{position:absolute;z-index:7;top:14px;right:16px;display:flex;align-items:center;padding:4px;border:1px solid #dce5ef;border-radius:10px;background:#fff;box-shadow:0 6px 17px rgba(15,38,70,.11)}.toolbar button,.zoom-readout{display:grid;width:31px;height:30px;border:0;border-radius:7px;background:transparent;place-items:center;color:#4e5f75;font-size:14px}.toolbar button{cursor:pointer}.toolbar button:hover{background:#edf3fa}.zoom-readout{width:52px;font-size:11px;font-variant-numeric:tabular-nums}
.catalog-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:21px 24px 16px;border-bottom:1px solid #e9eef4}.catalog-head h2{margin:0;color:var(--navy);font-size:19px;letter-spacing:-.03em}.catalog-head p{max-width:760px;margin:5px 0 0;color:#657287;font-size:12.5px;line-height:1.58}.catalog-count{color:#4e6076;font-size:12px;font-weight:850;white-space:nowrap}.catalog-guidance{margin:14px 20px 0;padding:10px 12px;border:1px solid #e2ecfa;border-radius:10px;background:#f5f9ff;color:#4c6381;font-size:12px;line-height:1.55}.catalog-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:16px 20px 20px}.catalog-card{min-width:0;padding:16px;border:1px solid #e0e7ef;border-radius:14px;background:#fff}.catalog-card.selected{border-color:#bfd5f6;background:linear-gradient(135deg,#fff 0%,#f5faff 100%)}.catalog-card.reference{border-color:#dbe4ee;background:#fbfcfe}.catalog-card.candidate{background:#fcfdfe}.catalog-card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:9px}.catalog-status{display:inline-flex;padding:4px 7px;border-radius:999px;background:#eaf2ff;color:#2767bb;font-size:10px;font-weight:900}.catalog-status.reference{background:#f3efff;color:#6555bd}.catalog-status.candidate{background:#f1f3f6;color:#667388}.catalog-stage{margin-top:8px;color:#667489;font-size:11px;font-weight:780}.catalog-card h3{margin:5px 0;color:#202d40;font-size:15px;line-height:1.4}.catalog-meta{color:#728095;font-size:11px;line-height:1.5}.catalog-description{margin:10px 0;color:#536177;font-size:12px;line-height:1.6}.catalog-reason{padding:9px 10px;border-radius:9px;background:#f5f8fc;color:#465771;font-size:11.5px;line-height:1.55}.catalog-evidence{display:block;margin-top:8px;color:#68778b;font-size:11px;line-height:1.5}.catalog-link,.drawer-link{display:inline-flex;margin-top:11px;color:#246bc8;font-size:12px;font-weight:850;text-decoration:none}.catalog-link:hover,.drawer-link:hover{text-decoration:underline}.catalog-link-unavailable{display:inline-flex;margin-top:11px;color:#8591a1;font-size:12px;font-weight:750}.catalog-more{grid-column:1/-1;border-top:1px solid #e9eef4;padding-top:12px}.catalog-more>summary{color:#52647c;font-size:12px;font-weight:850;cursor:pointer}.catalog-more-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}.catalog-empty{grid-column:1/-1;padding:22px 24px;color:#647187;font-size:13px;line-height:1.65}
.business-report-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:22px 24px 17px;border-bottom:1px solid #e6edf5;background:linear-gradient(135deg,#fbfdff 0%,#eef6ff 100%)}.business-report-kicker{color:var(--blue);font-size:10px;font-weight:900;letter-spacing:.12em}.business-report-head h2{margin:6px 0;color:var(--navy);font-size:21px;letter-spacing:-.04em}.business-report-lead{max-width:900px;margin:0;color:#53657d;font-size:13px;line-height:1.65}.business-report-state{flex:0 0 auto;padding:7px 9px;border:1px solid #cfe0f8;border-radius:999px;background:#fff;color:#2c67b8;font-size:11px;font-weight:850;white-space:nowrap}.business-report-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:17px 20px 20px}.narrative-section{min-width:0;padding:17px;border:1px solid #e1e8f0;border-radius:14px;background:#fff}.narrative-section.primary{grid-column:span 2;border-color:#bfd7f8;background:linear-gradient(135deg,#fff 0%,#f6fbff 100%)}.narrative-section h3{margin:0 0 8px;color:var(--navy);font-size:15px;letter-spacing:-.025em}.narrative-section p{margin:0;color:#4e6078;font-size:12.5px;line-height:1.65}.narrative-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-top:12px}.narrative-fact{padding:9px 10px;border:1px solid #ebeff4;border-radius:9px;background:#f7f9fc}.narrative-fact span{display:block;color:#758298;font-size:10px;font-weight:750}.narrative-fact strong{display:block;margin-top:3px;color:#263750;font-size:11.5px;line-height:1.47;overflow-wrap:anywhere}.narrative-list{margin:12px 0 0;padding:0;list-style:none}.narrative-list li{position:relative;margin:7px 0;padding-left:14px;color:#4d6079;font-size:12px;line-height:1.58}.narrative-list li:before{position:absolute;top:.65em;left:1px;width:6px;height:6px;border-radius:50%;background:var(--blue);content:""}.narrative-section[data-key="improvement_direction"] li:before,.narrative-section[data-key="to_be_operating_plan"] li:before{background:var(--orange)}.report-detail{grid-column:span 2;border:1px solid #e1e8f0;border-radius:13px;background:#fbfcfe}.report-detail>summary{display:flex;align-items:center;justify-content:space-between;padding:15px 16px;color:#263a57;font-size:13px;font-weight:850;cursor:pointer}.report-detail>summary::after{margin-left:10px;color:#76859a;font-size:11px;content:"자세히 보기"}.report-detail[open]>summary{border-bottom:1px solid #e7edf4}.report-detail[open]>summary::after{content:"접기"}.report-detail .narrative-section{border:0;border-radius:0;background:transparent}.narrative-empty{padding:24px;color:#647187;font-size:13px;line-height:1.65}
.support{margin-top:18px}.support h2{margin:0 0 10px;color:var(--navy);font-size:15px}.support details{margin-bottom:8px;border:1px solid #e0e7ef;border-radius:12px;background:#fff}.support summary{padding:13px 15px;color:#52637a;font-size:12px;font-weight:850;cursor:pointer}.support-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;padding:0 14px 14px}.support-item{min-height:88px;padding:12px;border:1px solid #e8edf3;border-radius:10px;background:#f9fbfd}.support-item strong{display:block;color:#2d3e57;font-size:12px;line-height:1.45}.support-item p{margin:5px 0 0;color:#68768a;font-size:11.5px;line-height:1.55}.technical-reference{margin-top:14px;border:1px solid #dce5ef;border-radius:12px;background:#fbfcfe}.technical-reference>summary{padding:13px 15px;color:#53647a;font-size:12px;font-weight:850;cursor:pointer}.technical-reference[open]>summary{border-bottom:1px solid #e5ebf2}.technical-reference-body{padding:0 2px 14px}.business-report-static{padding:16px 18px}.business-report-static h2{color:var(--navy);font-size:16px}.business-report-static h3{color:#283a56;font-size:13px}.business-report-static pre{max-height:420px;overflow:auto;padding:10px;border-radius:9px;background:#f5f8fb;color:#4b5d75;white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.55 inherit}.technical-reference.static-fallback{display:block}.static-fallback{display:block}.js .static-fallback{display:none}
.drawer-backdrop{position:fixed;z-index:19;inset:0;pointer-events:none;background:rgba(12,25,43,.28);opacity:0;transition:opacity .18s}.drawer-backdrop.open{pointer-events:auto;opacity:1}.drawer{position:fixed;z-index:20;top:16px;right:16px;bottom:16px;width:min(470px,calc(100vw - 32px));overflow:auto;border-radius:18px;background:#fff;box-shadow:0 24px 70px rgba(11,25,46,.24);transform:translateX(calc(100% + 40px));transition:transform .2s}.drawer.open{transform:none}.drawer-head{position:sticky;top:0;z-index:1;padding:20px;border-bottom:1px solid #e7edf4;background:rgba(255,255,255,.96);backdrop-filter:blur(10px)}.drawer-head-row{display:flex;justify-content:space-between;gap:12px}.drawer h2{margin:0;color:var(--navy);font-size:19px}.close{width:34px;height:34px;border:0;border-radius:9px;background:#eff3f7;color:#53647a;font-size:19px;cursor:pointer}.drawer-body{padding:8px 20px 24px}.detail{padding:14px 0;border-bottom:1px solid #edf1f5}.detail h3{margin:0 0 6px;color:#758399;font-size:11px}.detail .value{color:#3d4f67;font-size:13px;line-height:1.62;white-space:pre-wrap;overflow-wrap:anywhere}.node-id-note{display:block;margin-top:5px;color:#7b889a;font-size:10px}
@media(max-width:900px){.shell{padding:16px}.intro-main{padding:26px 22px 18px}.intro-side{padding:0 22px 22px}.intro h1{font-size:25px}.report-overview{grid-template-columns:1fr}.catalog-head{align-items:flex-start;flex-direction:column}.catalog-grid,.catalog-more-grid,.business-report-grid{grid-template-columns:1fr;padding:14px}.catalog-more{grid-column:auto}.narrative-section.primary,.report-detail{grid-column:auto}.support-list{grid-template-columns:1fr}.legend{max-width:none;justify-content:flex-start}.flow-head{align-items:flex-start;flex-direction:column}.graph-frame{height:455px}.tabs{overflow:auto}.tab{white-space:nowrap}}
@media(max-width:520px){.shell{padding:12px}.topbar{margin-bottom:12px}.meta{display:none}.intro{border-radius:16px}.intro h1{font-size:23px}.metric-grid,.narrative-facts{grid-template-columns:1fr}.graph-frame{height:410px}.catalog-grid,.business-report-grid{padding:12px}.flow-head,.catalog-head,.business-report-head{padding:17px}.business-report-head{flex-direction:column}.business-report-state{align-self:flex-start}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.01ms!important}}
@media print{body{background:#fff}.topbar,.tabs,.graph-frame,.toolbar,.drawer,.drawer-backdrop{display:none!important}.shell{max-width:none;padding:0}.intro,.report-overview,.flow-panel,.catalog-panel,.business-report,.support{box-shadow:none;break-inside:avoid}.static-fallback{display:block!important}.technical-reference>summary{display:none}.technical-reference-body{display:block!important}.business-report-static pre{max-height:none;overflow:visible}}
"""

JS = r"""
(()=>{'use strict';document.documentElement.classList.add('js');
const vm=JSON.parse(document.getElementById('report-data').textContent);const $=selector=>document.querySelector(selector);const shell=$('#report-shell');let allowedHosts=[];try{allowedHosts=JSON.parse(shell?.dataset.allowedHosts||'[]')}catch(_){allowedHosts=[]}if(!Array.isArray(allowedHosts))allowedHosts=[];
const clean=(value,limit=2400)=>typeof value==='string'?value.replace(/\s+/g,' ').trim().slice(0,limit):(typeof value==='number'||typeof value==='boolean'?String(value):'');const text=value=>value==null?'':String(value);const record=value=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);const sections=Array.isArray(vm.sections)?vm.sections:[];const businessSection=sections.find(section=>section?.section_id==='business_report');const catalogSection=sections.find(section=>section?.section_id==='catalog_recommendations');const catalogItems=Array.isArray(catalogSection?.items)?catalogSection.items.filter(record):[];
const stageTitles={'node-trigger-start':'업무 실행 시작','node-mail-ingest-sanitize':'메일 수집·정제','node-starrocks-query-guard':'데이터 조회·품질 점검','node-draft-synthesizer':'근거 기반 초안 작성','node-hitl-result-gate':'담당자 검토·승인','node-publish-and-notify':'승인본 게시·알림','node-pipeline-end':'업무 완료·이력 기록','node-failure-handler':'실패 처리·운영 알림'};const kindTitles={start:'업무 실행 시작',end:'업무 완료',work_step:'업무 처리',decision:'분기 판단',human_gate:'담당자 검토·승인',system_call:'시스템 연동',new_custom:'신규 자동화 처리',companion_service:'연계 서비스',skill_group:'AI Skill 처리',exception:'예외 처리'};const kindLabels={start:'업무 시작',end:'업무 완료',work_step:'업무 단계',decision:'분기 판단',human_gate:'사람 검토',system_call:'시스템 연동',new_custom:'신규 자동화',companion_service:'연계 서비스',skill_group:'AI Skill',exception:'예외 처리'};const sourceLabels={builtin:'Langflow 기본 요소',catalog_component:'카탈로그 Component',catalog_flow:'카탈로그 Flow',new_standalone_component:'신규 Standalone Component',companion_service:'연계 서비스',human_task:'사람 검토'};const statusLabels={design_only:'설계 초안',proposed_unverified:'사전 검증 필요',import_ready:'가져오기 준비 완료',metadata_only:'상세 계약 확인 필요',ports_extracted:'포트 계약 추출됨',flow_graph_extracted:'Flow 구조 추출됨',verified_runtime:'실행 검증됨'};const readerLabels={semi_automatic:'반자동 운영',fully_automated:'자동 운영',manual:'수동 운영',CredentialRef:'접근 권한 참조',DateRange:'기간 조건',Document:'문서',Message:'메시지',Data:'데이터'};
function safeUrl(value){const raw=clean(value,3000);if(!raw||/[\x00-\x1f\x7f]/.test(raw)||!/^[a-z][a-z0-9+.-]*:/i.test(raw))return null;try{const url=new URL(raw);const host=url.hostname.toLowerCase();if(!/^https?:$/.test(url.protocol)||!host||url.username||url.password||(allowedHosts.length&&!allowedHosts.includes(host)))return null;url.hash='';return url.href}catch(_){return null}}
function isTechnical(value){return /^(?:node[-_])?[a-z0-9]+(?:[-_][a-z0-9]+)+$/i.test(clean(value,180))}function displayTitle(node){const title=clean(node?.title,180);if(/[가-힣]/.test(title)&&!isTechnical(title))return title;return clean(node?.presentation_title,180)||stageTitles[node?.node_id]||kindTitles[node?.node_kind]||title||'업무 처리'}function detailFor(graph,node){return record(graph?.details?.[node?.detail_ref])?graph.details[node.detail_ref]:{}}function displaySummary(graph,node){const detail=detailFor(graph,node);const summary=graph?.graph_kind==='as_is'?clean(detail.current_work,420):clean(detail.improvement,420);return summary||clean(node?.summary,420)||'세부 운영 내용을 확인하고 다음 단계로 결과를 전달합니다.'}function nodeSort(nodes){return [...(Array.isArray(nodes)?nodes:[])].sort((a,b)=>(a.sequence??0)-(b.sequence??0)||text(a.node_id).localeCompare(text(b.node_id)))}function unique(values,maximum=18){return [...new Set(values.map(value=>clean(value,1200)).filter(Boolean))].slice(0,maximum)}
function scalarLine(value,depth=0){if(depth>2)return '';const primitive=clean(value,900);if(primitive)return readerLabels[primitive]||statusLabels[primitive]||primitive;if(Array.isArray(value))return unique(value.map(item=>scalarLine(item,depth+1)),5).join(' · ');if(!record(value))return '';const title=clean(value.title||value.name||value.label||value.asset_title||value.stage_title||value.role||value.system,240);const body=clean(value.summary||value.description||value.detail||value.reason||value.message||value.current_work||value.improvement||value.value,700);if(title&&body&&title!==body)return `${title} — ${body}`;return readerLabels[body]||title||body||''}const fieldLabels={goal:'업무 목표',trigger:'실행 시점',automation_intent:'자동화 의도',frequency_volume:'처리 주기·물량',sla:'완료 기준·SLA',scope_in:'포함 범위',scope_out:'제외 범위',actors:'담당 역할',systems:'연계 시스템',inputs:'입력 정보',outputs:'산출물',procedure:'현행 절차',pain_points:'주요 문제',decision_points:'판단 지점',exception_paths:'예외 처리',risks_controls:'위험·통제',objectives:'개선 목표',principles:'설계 원칙',recommended_procedure:'권장 절차',branch_and_exception_plan:'분기·예외 운영',human_review_points:'사람 검토 지점',catalog_reuse:'직접 적용 자산',catalog_candidates:'카탈로그 검토 후보',new_standalone_components:'신규 Standalone Component',builtin_components:'Langflow 기본 요소',companion_services:'연계 서비스',human_tasks:'사람 업무',skills:'적용 Skill',next_steps:'다음 단계',validation_plan:'검증 기준',open_items:'추가 확인 사항',approval_basis:'승인 근거',build_readiness:'구현 준비 상태'};
function normalizeBlock(value){const result={summary:'',facts:[],bullets:[]};if(typeof value==='string'||typeof value==='number'||typeof value==='boolean'){result.summary=scalarLine(value);return result}if(Array.isArray(value)){result.bullets=unique(value.map(item=>scalarLine(item)));return result}if(!record(value))return result;result.summary=clean(value.overview||value.summary||value.description||value.narrative||value.purpose||value.recommendation||value.goal,1200);for(const [key,item] of Object.entries(value)){if(['report_type','report_version','overview','summary','description','narrative','purpose','recommendation','message','title','schema_version','source'].includes(key)||item==null||item==='')continue;const label=fieldLabels[key]||'';if(typeof item==='string'||typeof item==='number'||typeof item==='boolean'){if(label)result.facts.push([label,readerLabels[clean(item)]||statusLabels[clean(item)]||clean(item,600)]);continue}if(Array.isArray(item)){for(const line of unique(item.map(entry=>scalarLine(entry))))result.bullets.push(label?`${label} · ${line}`:line);continue}if(record(item)){const line=scalarLine(item);if(line)result.bullets.push(label?`${label} · ${line}`:line)}}result.facts=result.facts.slice(0,6);result.bullets=unique(result.bullets);if(!result.summary&&result.facts.length)result.summary=result.facts[0][1];return result}
function metric(label,value){const card=document.createElement('div');card.className='metric';const key=document.createElement('span');key.textContent=label;const content=document.createElement('strong');content.textContent=text(value);card.append(key,content);return card}function renderOverview(){const graph=vm.to_be_graph||{};const nodes=Array.isArray(graph.nodes)?graph.nodes:[];const edges=Array.isArray(graph.edges)?graph.edges:[];const branchCount=edges.filter(edge=>edge&&(edge.edge_kind==='branch'||edge.edge_kind==='error'||edge.edge_kind==='retry'||edge.condition)).length;const selected=catalogItems.filter(asset=>asset.status==='selected_for_stage').length;const custom=nodes.filter(node=>node?.implementation_source==='new_standalone_component').length;const grid=$('#overview-metrics');if(grid){grid.replaceChildren(...[['TO-BE 업무 단계',nodes.length],['분기·예외 경로',branchCount],['직접 적용 자산',selected],['신규 Custom',custom]].map(([label,value])=>metric(label,value)))}const note=$('#overview-text');if(note){note.textContent=selected?`카탈로그에서 확인한 자산 ${selected}개를 설계 단계에 연결하고, 부족한 기능 ${custom}개는 Standalone Custom Component로 보완합니다.`:catalogItems.length?`카탈로그 후보 ${catalogItems.length}개를 단계별로 검토했습니다. 직접 재사용으로 확정되지 않은 후보는 포트·권한·실행 계약 확인 후에만 연결합니다.`:'현재 승인 범위에서 직접 재사용 자산을 찾지 못해, Langflow 기본 요소·신규 Standalone Component·사람 검토 단계를 조합합니다.'}}
const drawer=$('#drawer'),backdrop=$('#backdrop');function humanDetails(detail){const rows=[];const labels={current_work:'현재 업무',improvement:'개선 방식',reuse_decision_reason:'선정 이유',failure_policy:'실패 시 처리',human_review:'사람 검토',inputs:'입력 정보',outputs:'산출물',applied_skills:'적용 Skill'};for(const [key,label] of Object.entries(labels)){const value=detail?.[key];if(value==null||value===''||(Array.isArray(value)&&!value.length))continue;const line=scalarLine(value)||clean(value,2400);if(line)rows.push([label,line])}return rows}function openDrawer(title,detail,asset,node){if(!drawer||!backdrop)return;$('#drawer-title').textContent=title;const body=$('#drawer-body');body.replaceChildren();const rows=humanDetails(detail);if(node?.node_id){rows.push(['원본 단계 ID',clean(node.node_id,180)])}if(asset){rows.push(['카탈로그 적용 상태',asset.selection_status||(asset.status==='selected_for_stage'?'이 단계에 적용 권고':'검토 후보')]);if(asset.technical_contract_label||asset.technical_contract_status)rows.push(['연결 계약 상태',asset.technical_contract_label||statusLabels[asset.technical_contract_status]||asset.technical_contract_status]);if(asset.evidence_basis)rows.push(['근거',clean(asset.evidence_basis,1000)])}for(const [label,value] of rows){const section=document.createElement('section');section.className='detail';const heading=document.createElement('h3');heading.textContent=label;const content=document.createElement('div');content.className='value';content.textContent=value;section.append(heading,content);body.append(section)}const url=safeUrl(asset?.catalog_url);if(url){const link=document.createElement('a');link.className='drawer-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='카탈로그 상세 열기 ↗';body.append(link)}drawer.classList.add('open');backdrop.classList.add('open')}function closeDrawer(){drawer?.classList.remove('open');backdrop?.classList.remove('open')}$('#close')?.addEventListener('click',closeDrawer);backdrop?.addEventListener('click',closeDrawer);document.addEventListener('keydown',event=>{if(event.key==='Escape')closeDrawer()});
function catalogForNode(node){const ref=node?.asset_ref;const exact=catalogItems.find(asset=>asset?.target_node_id===node?.node_id||asset?.stage_node_id===node?.node_id);if(exact)return exact;if(record(ref)){const byRef=catalogItems.find(asset=>asset.asset_id===ref.asset_id&&asset.version===ref.version);if(byRef)return byRef}return catalogItems.find(asset=>asset.status==='selected_for_stage'&&asset.stage_title===displayTitle(node))||null}
function graphLayout(graph){const nodes=nodeSort(graph?.nodes);const byId=new Map(nodes.map(node=>[node.node_id,node]));const incoming=new Map(nodes.map(node=>[node.node_id,[]]));const outgoing=new Map(nodes.map(node=>[node.node_id,[]]));for(const edge of Array.isArray(graph?.edges)?graph.edges:[]){if(byId.has(edge?.source_node_id)&&byId.has(edge?.target_node_id)){incoming.get(edge.target_node_id).push(edge);outgoing.get(edge.source_node_id).push(edge)}}const rank=new Map(nodes.map(node=>[node.node_id,0]));const indegree=new Map(nodes.map(node=>[node.node_id,incoming.get(node.node_id).length]));let ready=nodes.filter(node=>indegree.get(node.node_id)===0);const seen=new Set();while(ready.length){ready.sort((a,b)=>(a.sequence??0)-(b.sequence??0)||text(a.node_id).localeCompare(text(b.node_id)));const node=ready.shift();if(seen.has(node.node_id))continue;seen.add(node.node_id);for(const edge of outgoing.get(node.node_id)||[]){const target=edge.target_node_id;rank.set(target,Math.max(rank.get(target)||0,(rank.get(node.node_id)||0)+1));indegree.set(target,(indegree.get(target)||1)-1);if(indegree.get(target)===0)ready.push(byId.get(target))}}for(const node of nodes){if(!seen.has(node.node_id))rank.set(node.node_id,Math.max(rank.get(node.node_id)||0,Math.max(0,(node.sequence??1)-1)))}const levels=new Map();for(const node of nodes){const level=rank.get(node.node_id)||0;if(!levels.has(level))levels.set(level,[]);levels.get(level).push(node)}const lane=new Map();for(const level of [...levels.keys()].sort((a,b)=>a-b)){const ordered=(levels.get(level)||[]).sort((a,b)=>(a.sequence??0)-(b.sequence??0)||text(a.node_id).localeCompare(text(b.node_id)));ordered.forEach((node,index)=>lane.set(node.node_id,index))}const W=244,H=162,gapX=150,gapY=102,left=56,top=84;let maxRank=0,maxLane=0;const pos=new Map();for(const node of nodes){const currentRank=rank.get(node.node_id)||0;const currentLane=lane.get(node.node_id)||0;maxRank=Math.max(maxRank,currentRank);maxLane=Math.max(maxLane,currentLane);pos.set(node.node_id,{x:left+currentRank*(W+gapX),y:top+currentLane*(H+gapY)})}return{nodes,byId,pos,W,H,width:Math.max(470,left*2+maxRank*(W+gapX)+W),height:Math.max(365,top*2+maxLane*(H+gapY)+H)}}
function compactGraphLayout(graph){const base=graphLayout(graph);const nodes=base.nodes;if(nodes.length<6)return base;const columns=4,rankGap=base.W+150,laneGap=base.H+102;const ranks=new Map(),lanes=new Map(),blockLanes=new Map();let maxRank=0;for(const node of nodes){const original=base.pos.get(node.node_id);const rank=Math.max(0,Math.round((original.x-56)/rankGap));const lane=Math.max(0,Math.round((original.y-84)/laneGap));const block=Math.floor(rank/columns);ranks.set(node.node_id,rank);lanes.set(node.node_id,lane);blockLanes.set(block,Math.max(blockLanes.get(block)||0,lane+1));maxRank=Math.max(maxRank,rank)}const blockTop=new Map();let cursor=0;for(let block=0;block<=Math.floor(maxRank/columns);block+=1){blockTop.set(block,cursor);cursor+=(blockLanes.get(block)||1)*laneGap+52}const pos=new Map();for(const node of nodes){const rank=ranks.get(node.node_id)||0,block=Math.floor(rank/columns),within=rank%columns,column=block%2?columns-1-within:within;pos.set(node.node_id,{x:56+column*rankGap,y:84+(blockTop.get(block)||0)+(lanes.get(node.node_id)||0)*laneGap})}return{...base,pos,width:Math.max(470,56*2+Math.min(columns,maxRank+1)*rankGap-150),height:Math.max(365,84*2+cursor-52)}}
function renderGraph(graph,host){const frame=document.createElement('div');frame.className='graph-frame';const viewport=document.createElement('div');viewport.className='graph-viewport';const world=document.createElement('div');world.className='graph-world';const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('edge-layer');const layer=document.createElement('div');layer.className='node-layer';world.append(svg,layer);viewport.append(world);frame.append(viewport);const toolbar=document.createElement('div');toolbar.className='toolbar';const minus=document.createElement('button'),read=document.createElement('div'),plus=document.createElement('button'),fit=document.createElement('button');minus.type=plus.type=fit.type='button';minus.textContent='−';plus.textContent='+';fit.textContent='↙';fit.title='전체 흐름 보기';fit.setAttribute('aria-label','전체 흐름 보기');read.className='zoom-readout';toolbar.append(minus,read,plus,fit);frame.append(toolbar);host.append(frame);const layout=compactGraphLayout(graph);world.style.width=`${layout.width}px`;world.style.height=`${layout.height}px`;svg.setAttribute('width',layout.width);svg.setAttribute('height',layout.height);svg.setAttribute('viewBox',`0 0 ${layout.width} ${layout.height}`);let scale=1;function apply(){world.style.transform=`scale(${scale})`;read.textContent=`${Math.round(scale*100)}%`}function zoom(delta){scale=Math.max(.28,Math.min(1.6,+(scale+delta).toFixed(2)));apply()}function fitAll(){const horizontal=Math.max(.01,(viewport.clientWidth-56)/layout.width);const vertical=Math.max(.01,(viewport.clientHeight-56)/layout.height);const allNodesScale=Math.min(1,horizontal,vertical);scale=viewport.clientWidth<620?Math.max(.72,allNodesScale):Math.max(.62,allNodesScale);apply();viewport.scrollTo({left:0,top:0})}minus.addEventListener('click',()=>zoom(-.1));plus.addEventListener('click',()=>zoom(.1));fit.addEventListener('click',fitAll);const defs=document.createElementNS('http://www.w3.org/2000/svg','defs');const marker=document.createElementNS('http://www.w3.org/2000/svg','marker');marker.id='flow-arrow';[['viewBox','0 0 10 10'],['refX','9'],['refY','5'],['markerWidth','6'],['markerHeight','6'],['orient','auto']].forEach(([name,value])=>marker.setAttribute(name,value));const arrow=document.createElementNS('http://www.w3.org/2000/svg','path');arrow.setAttribute('d','M0 0 L10 5 L0 10z');arrow.setAttribute('fill','#96a7ba');marker.append(arrow);defs.append(marker);svg.append(defs);for(const edge of Array.isArray(graph?.edges)?graph.edges:[]){const source=layout.pos.get(edge?.source_node_id),target=layout.pos.get(edge?.target_node_id);if(!source||!target)continue;const sx=source.x+layout.W,sy=source.y+layout.H/2,tx=target.x,ty=target.y+layout.H/2,curve=Math.max(64,Math.abs(tx-sx)*.46);const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.classList.add('edge-path',`edge-${edge.edge_kind||'control'}`);path.setAttribute('d',tx<=sx?`M${sx} ${sy} C${sx+curve} ${sy},${tx+layout.W+curve} ${ty},${tx} ${ty}`:`M${sx} ${sy} C${sx+curve} ${sy},${tx-curve} ${ty},${tx} ${ty}`);path.setAttribute('marker-end','url(#flow-arrow)');svg.append(path);const label=document.createElement('button');label.type='button';label.className='edge-label';label.textContent=clean(edge.label,120)||'다음 단계';label.style.left=`${(sx+tx)/2}px`;label.style.top=`${(sy+ty)/2}px`;label.addEventListener('click',()=>openDrawer(clean(edge.label,180)||'연결 정보',{current_work:`${displayTitle(layout.byId.get(edge.source_node_id))} → ${displayTitle(layout.byId.get(edge.target_node_id))}`,improvement:edge.condition?`분기 조건: ${clean(edge.condition,700)}`:'순서에 따라 다음 업무 단계로 전달합니다.'},null,null));layer.append(label)}for(const node of layout.nodes){const position=layout.pos.get(node.node_id);const article=document.createElement('article');article.className=`flow-node ${node.node_kind||''}`;article.style.left=`${position.x}px`;article.style.top=`${position.y}px`;const button=document.createElement('button');button.type='button';button.className='node-main';const top=document.createElement('div');top.className='node-top';const stage=document.createElement('span');stage.textContent=kindLabels[node.node_kind]||'업무 단계';const step=document.createElement('span');step.textContent=`${node.sequence??'-'}단계`;top.append(stage,step);const heading=document.createElement('h3');heading.textContent=displayTitle(node);const summary=document.createElement('p');summary.textContent=displaySummary(graph,node);const badges=document.createElement('div');badges.className='node-badges';const source=document.createElement('span');source.className=`pill${node.implementation_source==='new_standalone_component'?' new':''}`;source.textContent=sourceLabels[node.implementation_source]||node.implementation_label||'구현 방식';badges.append(source);if(Array.isArray(node.applied_skills)&&node.applied_skills.length){const skill=document.createElement('span');skill.className='pill skill';skill.textContent='AI Skill';badges.append(skill)}button.append(top,heading,summary,badges);const asset=catalogForNode(node);button.addEventListener('click',()=>openDrawer(displayTitle(node),detailFor(graph,node),asset,node));article.append(button);const url=safeUrl(asset?.catalog_url);if(url){const link=document.createElement('a');link.className='node-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=`카탈로그: ${clean(asset.asset_title||asset.asset_id,80)} ↗`;article.append(link)}layer.append(article)}requestAnimationFrame(fitAll);let dragging=false,startX=0,startY=0,scrollLeft=0,scrollTop=0;viewport.addEventListener('pointerdown',event=>{if(event.target.closest('button,a'))return;dragging=true;startX=event.clientX;startY=event.clientY;scrollLeft=viewport.scrollLeft;scrollTop=viewport.scrollTop;viewport.setPointerCapture(event.pointerId)});viewport.addEventListener('pointermove',event=>{if(dragging){viewport.scrollLeft=scrollLeft-(event.clientX-startX);viewport.scrollTop=scrollTop-(event.clientY-startY)}});viewport.addEventListener('pointerup',()=>{dragging=false});viewport.addEventListener('wheel',event=>{if(event.ctrlKey||event.metaKey){event.preventDefault();zoom(event.deltaY<0?.1:-.1)}},{passive:false});return fitAll}
function statusFor(asset){if(asset.status==='selected_for_stage')return['이 단계에 적용 권고','selected'];if(asset.status==='reference_candidate_for_stage')return['연결 검토 후보','reference'];return['검토 후보','candidate']}function catalogCard(asset){const card=document.createElement('article');const [label,kind]=statusFor(asset);card.className=`catalog-card ${kind}`;const top=document.createElement('div');top.className='catalog-card-top';const status=document.createElement('span');status.className=`catalog-status ${kind}`;status.textContent=asset.selection_status||label;const contract=document.createElement('span');contract.className='catalog-meta';contract.textContent=asset.technical_contract_label||statusLabels[asset.technical_contract_status]||clean(asset.technical_contract_status,80)||'계약 확인 필요';top.append(status,contract);const stage=document.createElement('div');stage.className='catalog-stage';stage.textContent=`적용 위치 · ${clean(asset.stage_title,160)||'후보 검토'}`;const title=document.createElement('h3');title.textContent=clean(asset.asset_title||asset.asset_id,300)||'카탈로그 자산';const meta=document.createElement('div');meta.className='catalog-meta';meta.textContent=[clean(asset.category,100),clean(asset.asset_type,50),clean(asset.version,50),clean(asset.asset_id,100)].filter(Boolean).join(' · ');const description=document.createElement('p');description.className='catalog-description';description.textContent=clean(asset.description||asset.stage_summary,900);const reason=document.createElement('div');reason.className='catalog-reason';reason.textContent=`선정/검토 이유 · ${clean(asset.reuse_decision_reason||asset.stage_summary,1200)||'업무 요구와 카탈로그 계약을 기준으로 검토합니다.'}`;card.append(top,stage,title,meta);if(description.textContent)card.append(description);card.append(reason);const evidence=clean(asset.evidence_basis,700);if(evidence){const hint=document.createElement('span');hint.className='catalog-evidence';hint.textContent=`근거 · ${evidence}`;card.append(hint)}const url=safeUrl(asset.catalog_url);if(url){const link=document.createElement('a');link.className='catalog-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='카탈로그 상세 열기 ↗';card.append(link)}else{const unavailable=document.createElement('span');unavailable.className='catalog-link-unavailable';unavailable.textContent='상세 링크 미등록';card.append(unavailable)}return card}function renderCatalog(){const host=$('#catalog-cards'),count=$('#catalog-count');if(!host||!count)return;host.replaceChildren();const selected=catalogItems.filter(asset=>asset.status==='selected_for_stage');const references=catalogItems.filter(asset=>asset.status==='reference_candidate_for_stage');const candidates=catalogItems.filter(asset=>!selected.includes(asset)&&!references.includes(asset));count.textContent=catalogItems.length?`${selected.length}개 적용 권고 · ${catalogItems.length-selected.length}개 검토 후보`:'카탈로그 자산 없음';const old=$('.catalog-guidance');old?.remove();const guide=document.createElement('p');guide.className='catalog-guidance';guide.textContent=selected.length?'적용 권고 자산은 현재 설계 단계와 연결됩니다. 나머지 후보는 상세 포트·권한 계약을 확인한 뒤에만 실제 Flow에 연결합니다.':'현재 결과는 재사용을 확정한 것이 아니라 단계별 검토 후보입니다. 상세 포트·권한·실행 계약 확인 후에만 실제 Flow에 연결합니다.';host.parentElement?.insertBefore(guide,host);if(!catalogItems.length){const empty=document.createElement('div');empty.className='catalog-empty';empty.textContent='현재 승인 범위에서 직접 재사용 가능한 카탈로그 자산을 찾지 못했습니다. 신규 Standalone Component와 Langflow 기본 요소를 조합해 구현합니다.';host.append(empty);return}const preferred=[...selected,...references];preferred.forEach(asset=>host.append(catalogCard(asset)));const visibleCandidates=candidates.slice(0,6);visibleCandidates.forEach(asset=>host.append(catalogCard(asset)));const remaining=candidates.slice(6);if(remaining.length){const details=document.createElement('details');details.className='catalog-more';const summary=document.createElement('summary');summary.textContent=`추가 검토 후보 ${remaining.length}개 보기`;const grid=document.createElement('div');grid.className='catalog-more-grid';remaining.forEach(asset=>grid.append(catalogCard(asset)));details.append(summary,grid);host.append(details)}}
function reportBlock(key,heading,block,primary=false){const section=document.createElement('section');section.className=`narrative-section${primary?' primary':''}`;section.dataset.key=key;const title=document.createElement('h3');title.textContent=heading;section.append(title);if(block.summary){const summary=document.createElement('p');summary.textContent=block.summary;section.append(summary)}if(block.facts.length){const facts=document.createElement('div');facts.className='narrative-facts';block.facts.forEach(([label,value])=>{const card=document.createElement('div');card.className='narrative-fact';const keyNode=document.createElement('span');keyNode.textContent=label;const valueNode=document.createElement('strong');valueNode.textContent=value;card.append(keyNode,valueNode);facts.append(card)});section.append(facts)}if(block.bullets.length){const list=document.createElement('ul');list.className='narrative-list';block.bullets.slice(0,8).forEach(line=>{const item=document.createElement('li');item.textContent=line;list.append(item)});section.append(list)}return section}function renderBusinessReport(){const host=$('#business-report');if(!host)return;host.replaceChildren();const reportItems=Array.isArray(businessSection?.items)?businessSection.items:[];const report=reportItems.find(item=>record(item)&&item.report_type==='business_report')||reportItems.find(record)||{};const header=document.createElement('header');header.className='business-report-head';const copy=document.createElement('div');const kicker=document.createElement('div');kicker.className='business-report-kicker';kicker.textContent='BUSINESS WORK DESIGN REPORT';const title=document.createElement('h2');title.textContent=clean(businessSection?.title,300)||'업무 방식 및 개선 실행 보고서';const lead=document.createElement('p');lead.className='business-report-lead';lead.textContent=clean(report?.executive_summary?.overview||report?.executive_summary?.summary||vm.summary?.pattern_reason,1200)||'업무 정의, 개선 방향, 카탈로그 적용 계획, 운영 검증 기준을 하나의 보고서로 정리했습니다.';copy.append(kicker,title,lead);const state=document.createElement('div');state.className='business-report-state';state.textContent=reportItems.length?'승인된 설계 기반':'Flow 기반 초안';header.append(copy,state);host.append(header);const grid=document.createElement('div');grid.className='business-report-grid';const definitions=[['executive_summary','업무 개요',true],['work_overview','업무 범위와 목표',false],['improvement_direction','개선 방향',false]];let visible=0;for(const [key,heading,primary] of definitions){const block=normalizeBlock(report?.[key]);if(!block.summary&&!block.facts.length&&!block.bullets.length)continue;grid.append(reportBlock(key,heading,block,primary));visible+=1}const details=[['operating_context','운영 대상과 입출력'],['as_is_analysis','현행 절차 및 문제'],['to_be_operating_plan','권장 운영 방식'],['implementation_allocation','구현 분담 및 카탈로그 적용'],['next_steps','구현 로드맵'],['validation_plan','검증 기준'],['open_items','추가 확인 사항']];for(const [key,heading] of details){const block=normalizeBlock(report?.[key]);if(!block.summary&&!block.facts.length&&!block.bullets.length)continue;const detail=document.createElement('details');detail.className='report-detail';const summary=document.createElement('summary');summary.textContent=`${heading}${block.bullets.length?` · ${block.bullets.length}개 항목`:''}`;detail.append(summary,reportBlock(key,heading,block));grid.append(detail)}if(!visible){const empty=document.createElement('div');empty.className='narrative-empty';empty.textContent='완성 업무 설계 데이터가 아직 준비되지 않았습니다. 위 Flow와 카탈로그 적용 계획을 기준으로 초안을 확인할 수 있습니다.';grid.append(empty)}host.append(grid);host.setAttribute('aria-busy','false')}
function renderSupport(){const host=$('#support');if(!host)return;host.replaceChildren();for(const section of sections.filter(item=>item&&item.section_id!=='business_report'&&item.section_id!=='catalog_recommendations'&&item.section_id!=='catalog_reuse')){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=clean(section.title,220)||'설계 참고 정보';details.append(summary);const list=document.createElement('div');list.className='support-list';const items=Array.isArray(section.items)?section.items:[];if(!items.length){const empty=document.createElement('div');empty.className='support-item';empty.textContent='현재 추가 확인 사항이 없습니다.';list.append(empty)}else{items.slice(0,12).forEach(item=>{const row=document.createElement('div');row.className='support-item';const title=document.createElement('strong');const body=document.createElement('p');if(typeof item==='string'){title.textContent=clean(item,360)}else{title.textContent=clean(item?.name||item?.title||item?.test_id||item?.description,260)||'항목';body.textContent=clean(item?.risk&&item?.control?`주의: ${item.risk} · 대응: ${item.control}`:item?.description||item?.message||item?.control,600)}row.append(title);if(body.textContent)row.append(body);list.append(row)})}details.append(list);host.append(details)}}
$('#page-title').textContent=clean(vm.title,500)||'업무 흐름을 더 단순하게, Agent와 함께.';$('#page-desc').textContent='반복 작업은 Agent가 처리하고, 중요한 판단과 승인은 사람이 담당하는 업무 구조를 확인합니다.';$('#reason').textContent=clean(vm.summary?.pattern_reason,1200)||'업무 단계별 자동화 범위와 사람 검토 지점을 분리해 운영 안정성을 확보합니다.';$('#approval-meta').textContent=vm.summary?.approval_status==='APPROVED'?'승인됨':clean(vm.summary?.approval_status,80)||'-';$('#revision-meta').textContent=`Rev. ${vm.summary?.work_definition_revision??'-'}`;renderOverview();renderCatalog();renderBusinessReport();renderSupport();
let activeFit=null;function render(kind){const graph=kind==='as_is'?vm.as_is_graph||{}:vm.to_be_graph||{};$('#flow-kicker').textContent=kind==='as_is'?'CURRENT WORK':'WITH AGENT';$('#flow-title').textContent=kind==='as_is'?'현재 업무 흐름':'Agent 적용 후 업무 흐름';$('#flow-desc').textContent=kind==='as_is'?'현재 사람이 수행하는 실제 업무 단계를 확인합니다.':'반복 작업은 자동화하고, 승인·예외 판단은 사람이 통제하는 권장 운영 방식입니다.';const legend=$('#legend');legend.replaceChildren();const labels=[...new Set((Array.isArray(graph.nodes)?graph.nodes:[]).map(node=>sourceLabels[node?.implementation_source]||node?.implementation_label).filter(Boolean))];labels.forEach(label=>{const pill=document.createElement('span');pill.className=`pill${label==='신규 Standalone Component'||label==='신규 Custom'?' new':''}`;pill.textContent=label;legend.append(pill)});if((Array.isArray(graph.nodes)?graph.nodes:[]).some(node=>Array.isArray(node?.applied_skills)&&node.applied_skills.length)){const skill=document.createElement('span');skill.className='pill skill';skill.textContent='AI Skill 적용';legend.append(skill)}const host=$('#graph-host');host.replaceChildren();activeFit=renderGraph(graph,host)}render('to_be');$('.tabs')?.addEventListener('click',event=>{const tab=event.target.closest('.tab');if(!tab)return;document.querySelectorAll('.tab').forEach(item=>item.classList.toggle('active',item===tab));render(tab.dataset.tab)});let resizeTimer=0;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=window.setTimeout(()=>activeFit?.(),120)});
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


def _business_report_payload(view_model: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Return the sealed narrative item without widening the report schema.

    F30 keeps the closed top-level schema; a report narrative therefore lives
    in ``sections[]`` rather than becoming an unsealed top-level blob.  The
    renderer also accepts a first object item for backwards-compatible drafts
    while preferring the explicit ``report_type`` marker.
    """

    for section in view_model.get("sections", []):
        if not isinstance(section, dict) or section.get("section_id") != "business_report":
            continue
        title = _text(section.get("title"), 500).strip() or "완성 업무 설계 보고서"
        items = section.get("items")
        if not isinstance(items, list):
            return title, None
        for item in items:
            if isinstance(item, dict) and item.get("report_type") == "business_report":
                return title, item
        for item in items:
            if isinstance(item, dict):
                return title, item
        return title, None
    return "완성 업무 설계 보고서", None


def _static_business_report(view_model: dict[str, Any]) -> str:
    """Safe, readable no-JS/print fallback for the primary report narrative."""

    title, payload = _business_report_payload(view_model)
    headings = (
        ("executive_summary", "업무 개요"),
        ("work_overview", "업무 범위와 목표"),
        ("operating_context", "운영 대상과 입출력"),
        ("as_is_analysis", "현행 절차 및 문제"),
        ("improvement_direction", "개선 방향"),
        ("to_be_operating_plan", "권장 운영 방식"),
        ("implementation_allocation", "구현 분담 및 카탈로그 적용"),
        ("next_steps", "구현 로드맵"),
        ("validation_plan", "검증 기준"),
        ("open_items", "추가 확인 사항"),
    )
    blocks = [f"<div class=\"business-report-static\"><h2 id=\"business-report-title\">{html.escape(title)}</h2>"]
    if not isinstance(payload, dict):
        blocks.append(
            "<p>업무 정의 기반 완성 보고서 데이터가 아직 준비되지 않았습니다. "
            "아래 업무 Flow와 카탈로그 적용 계획을 기준으로 초안을 확인하세요.</p>"
        )
        blocks.append("</div>")
        return "".join(blocks)
    for key, heading in headings:
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        try:
            material = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        except (TypeError, ValueError):
            material = _text(value, 20_000)
        blocks.append(f"<h3>{html.escape(heading)}</h3><pre>{html.escape(material[:20_000])}</pre>")
    blocks.append("</div>")
    return "".join(blocks)


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
        link_policy_json = html.escape(
            json.dumps(allowed_hosts, ensure_ascii=False, separators=(",", ":")),
            quote=True,
        )
        summary = view_model.get("summary") if isinstance(view_model.get("summary"), dict) else {}
        summary_cards = "".join(
            f"<div class=\"summary-card\"><span>{html.escape(_text(key,128))}</span><strong>{html.escape(_text(value,2000))}</strong></div>"
            for key, value in sorted(summary.items(), key=lambda item: item[0])
        )
        support = "".join(
            f"<details><summary>{html.escape(_text(section.get('title'),500))}</summary><pre>{html.escape(json.dumps(section.get('items'),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)[:100_000])}</pre></details>"
            for section in view_model.get("sections", [])
            if isinstance(section, dict) and section.get("section_id") != "business_report"
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
        business_report_static = _static_business_report(view_model)
        json_payload = _escaped_json(view_model)
        document = (
            "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta name=\"renderer-version\" content=\"{html.escape(renderer_version)}\">"
            f"<title>{html.escape(_text(view_model.get('title'), 500))}</title>"
            f"<style>{CSS}</style></head><body>"
            f"<main id=\"report-shell\" class=\"shell\" data-allowed-hosts=\"{link_policy_json}\">"
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
            "<section class=\"report-overview\" aria-label=\"설계 요약\">"
            "<article class=\"overview-card\"><h2>설계 한눈에 보기</h2><div id=\"overview-metrics\" class=\"metric-grid\"></div></article>"
            "<article class=\"overview-note\"><h2>적용 방향</h2><p id=\"overview-text\"></p></article>"
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
            "<section class=\"catalog-panel\" aria-labelledby=\"catalog-title\">"
            "<div class=\"catalog-head\"><div><h2 id=\"catalog-title\">카탈로그 기반 적용 계획</h2><p>어느 업무 단계에 어떤 기존 Component·Flow를 적용하는지, 선택 이유와 상세 링크를 함께 확인합니다.</p></div><div id=\"catalog-count\" class=\"catalog-count\"></div></div>"
            "<div id=\"catalog-cards\" class=\"catalog-grid\"></div>"
            "</section>"
            "<section id=\"business-report\" class=\"business-report\" aria-live=\"polite\" aria-busy=\"true\"></section>"
            "<section class=\"support\" aria-labelledby=\"design-reference-title\">"
            "<h2 id=\"design-reference-title\">설계 참고 정보</h2><div id=\"support\"></div>"
            "<details class=\"technical-reference static-fallback\">"
            "<summary>원시 데이터 및 순서형 전체 내용 (기술 참고)</summary>"
            f"<div class=\"technical-reference-body\">{business_report_static}{static_fallback}{support}</div>"
            "</details></section>"
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
