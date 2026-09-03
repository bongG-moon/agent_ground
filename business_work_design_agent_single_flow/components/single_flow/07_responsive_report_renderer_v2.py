from __future__ import annotations

"""Render a safe, polished, self-contained Korean business-design report.

The LLM never supplies HTML, CSS, or JavaScript.  This standalone component
only renders the closed report-view-model/v2 payload with fixed code.
"""

import base64
import datetime as _dt
import hashlib
import json
import math
import re
import urllib.parse
import uuid
from decimal import Decimal
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema import Data


_SCHEMA = "report-view-model/v2"
_RENDERER = "business-report-renderer.v2"
_REPORT_ID = re.compile(r"^report-[0-9a-f]{24}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|client[_-]?secret|cookie|credential|password|passwd|private[_-]?key|secret|token)")
_SECRET_VALUE = re.compile(r"(?i)(?:\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]{8,}|\bbearer\s+\S{8,}|\bsk-[A-Za-z0-9_-]{16,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def _safe_json(value: Any, path: str = "$") -> Any:
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return _safe_json(data, path)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"[REPORT_RENDER_FAILED] {path}에 유한하지 않은 숫자가 있습니다.")
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"[REPORT_RENDER_FAILED] {path}에 유한하지 않은 숫자가 있습니다.")
        return value
    if isinstance(value, (tuple, set)):
        return [_safe_json(item, f"{path}[]") for item in value]
    if isinstance(value, list):
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"[REPORT_RENDER_FAILED] {path}에 문자열이 아닌 key가 있습니다.")
            converted[key] = _safe_json(item, f"{path}.{key}")
        return converted
    raise ValueError(f"[REPORT_RENDER_FAILED] {path}의 값 형식을 처리할 수 없습니다.")


def _canonical(value: Any) -> str:
    return json.dumps(_safe_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _csp_hash(value: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)) and item not in (None, "", False, "[REDACTED]"):
                return True
            if _contains_secret(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and value != "[REDACTED]" and bool(_SECRET_VALUE.search(value))


def _payload(value: Any) -> dict[str, Any]:
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("[REPORT_RENDER_FAILED] Report View Model이 JSON object가 아닙니다. 06 node 연결을 확인해 주세요.") from exc
    if not isinstance(value, dict):
        raise ValueError("[REPORT_RENDER_FAILED] Report View Model이 없습니다. 06 node 연결을 확인해 주세요.")
    return _safe_json(value, "report_view_model")


def _expected_report_id(view_model: dict[str, Any]) -> str:
    material = {key: value for key, value in view_model.items() if key != "report_id"}
    return "report-" + _sha256(_canonical(material))[:24]


def _valid_catalog_url(asset_id: Any, asset_type: Any, url: Any) -> bool:
    asset_id_text = str(asset_id or "").lower()
    normalized_type = "flow" if str(asset_type or "").casefold() == "flow" else "component"
    if _UUID.fullmatch(asset_id_text) is None or not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "agent-hub.skhynix.com"
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and parsed.path in {"", "/"}
        and parsed.fragment == f"/{normalized_type}/{asset_id_text}"
    )


def _escaped_json(value: dict[str, Any]) -> str:
    return _canonical(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _scrub_catalog_urls(value: Any) -> Any:
    """Remove an invalid catalog URL before it can enter HTML/JS payload data.

    The normalizer and view-model builder always produce the canonical Agent
    Hub URL.  This second boundary protects the renderer when a manually
    supplied Data object bypasses those upstream custom components.
    """

    if isinstance(value, list):
        return [_scrub_catalog_urls(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _scrub_catalog_urls(item) for key, item in value.items()}
    if "catalog_url" in result and not _valid_catalog_url(result.get("asset_id"), result.get("asset_type"), result.get("catalog_url")):
        result["catalog_url"] = ""
    return result


CSS = r"""
:root{--bg:#f4f6f8;--paper:#fff;--ink:#17202a;--muted:#6f7885;--line:#e3e8ee;--orange:#ef5b2a;--orange-soft:#fff0ea;--violet:#7257e8;--violet-soft:#f1eeff;--green:#13795b;--green-soft:#eaf8f2;--amber:#a65b00;--amber-soft:#fff4df;--red:#a93f3f;--red-soft:#fff0f0;--shadow:0 12px 32px rgba(23,32,42,.08);--radius:18px}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Noto Sans KR","Segoe UI",Arial,sans-serif;letter-spacing:-.015em;line-height:1.5}.shell{width:min(1500px,100%);margin:0 auto;padding:28px 32px 80px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 3px 15px;border-bottom:1px solid #dce2e8}.brand{display:flex;gap:9px;align-items:center;font-weight:800}.mark{width:27px;height:27px;display:grid;place-items:center;color:#fff;background:#222831;border-radius:8px;font-size:12px}.meta{font-size:12px;color:var(--muted)}.hero{margin:20px 0;background:var(--paper);border:1px solid var(--line);border-radius:22px;overflow:hidden;box-shadow:var(--shadow)}.hero-main{padding:27px 30px 20px}.eyebrow{font-size:11px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;color:var(--orange)}h1{font-size:28px;line-height:1.25;margin:6px 0 8px}h2{font-size:20px;line-height:1.3;margin:0}h3{font-size:15px;margin:0}.lead{margin:0;color:var(--muted);font-size:14px}.hero-status{display:flex;border-top:1px solid var(--line)}.status-card{min-width:220px;padding:17px 22px;border-right:1px solid var(--line);font-size:13px;font-weight:750}.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:7px}.quick{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:13px 18px}.quick span{font-size:12px;padding:6px 9px;border-radius:7px;background:#f3f5f7;color:var(--muted)}.quick b{color:var(--ink);margin-left:4px}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}.card{background:var(--paper);border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 5px 18px rgba(23,32,42,.045)}.section{padding:21px 23px}.section-title{display:flex;align-items:start;justify-content:space-between;gap:12px;margin-bottom:12px}.section-title p{margin:4px 0 0;color:var(--muted);font-size:13px}.wide{grid-column:span 12}.half{grid-column:span 6}.third{grid-column:span 4}.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}.summary-item{border:1px solid #edf0f4;border-radius:12px;padding:13px;background:#fcfcfd}.summary-item h3{font-size:13px}.summary-item p{margin:6px 0 0;color:var(--muted);font-size:12.5px;white-space:pre-wrap}.badges{display:flex;gap:6px;flex-wrap:wrap}.badge{display:inline-flex;align-items:center;width:max-content;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:760}.badge.green{background:var(--green-soft);color:var(--green)}.badge.orange{background:var(--orange-soft);color:#c24218}.badge.violet{background:var(--violet-soft);color:#5f43d5}.badge.amber{background:var(--amber-soft);color:var(--amber)}.badge.red{background:var(--red-soft);color:var(--red)}.source{white-space:pre-wrap;word-break:break-word;color:#303942;background:#fbfcfd;border:1px solid #e8ecf0;border-radius:12px;padding:16px;font-size:13px;line-height:1.7}.note{color:var(--muted);font-size:12px;margin:9px 0 0}.gap-list{display:grid;gap:10px}.gap{padding:14px;border:1px solid #eee5d5;border-left:4px solid var(--amber);border-radius:12px;background:#fffdf8}.gap.required{border-left-color:var(--red);background:#fffafa}.gap.optional{border-left-color:var(--violet);background:#fcfbff}.gap h3{font-size:14px;margin-bottom:7px}.gap p{font-size:12.5px;margin:5px 0;color:#505a66}.copy-box{margin-top:9px;padding:9px 10px;border-radius:9px;background:#f7f8fa;font-size:12px;color:#344050;white-space:pre-wrap}.facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}.fact{border-left:3px solid #d5dae1;padding:7px 10px;background:#fafbfc;border-radius:0 8px 8px 0;font-size:12px}.fact b{display:block;color:#6f7885;font-size:10px;margin-bottom:2px}.bullet-list{padding-left:19px;margin:9px 0 0;color:#4f5965;font-size:12.5px}.bullet-list li{margin:5px 0}.tabs{display:flex;gap:22px;border-bottom:1px solid #dfe4ea;margin:0 0 12px}.tab{appearance:none;border:0;background:transparent;padding:10px 2px;color:#8a939f;font-size:13px;font-weight:750;cursor:pointer;border-bottom:2px solid transparent}.tab.active{color:var(--orange);border-bottom-color:var(--orange)}.graph-frame{height:440px;position:relative;border:1px solid #eef1f4;border-radius:15px;background:#fafbfd;overflow:hidden}.graph-viewport{position:absolute;inset:0;overflow:auto;cursor:grab}.graph-world{position:relative;transform-origin:0 0}.edges{position:absolute;inset:0;pointer-events:none;overflow:visible}.node-layer{position:absolute;inset:0}.flow-node{position:absolute;width:208px;min-height:130px;border:1px solid #e2e7ed;background:#fff;border-radius:15px;box-shadow:0 7px 18px rgba(25,32,40,.07);padding:0;text-align:left;overflow:hidden;cursor:pointer;color:inherit}.flow-node:focus{outline:3px solid rgba(114,87,232,.32);outline-offset:2px}.flow-node .stripe{height:4px;background:#9ea8b4}.flow-node.decision .stripe{background:#d59b30}.flow-node.exception .stripe{background:#ce5555}.flow-node.human_review .stripe{background:#7257e8}.flow-node.start .stripe,.flow-node.end .stripe{background:#2aa176}.node-inner{padding:12px 13px}.node-meta{display:flex;justify-content:space-between;color:#8a939f;font-size:10px}.node-inner h3{font-size:14px;margin:8px 0 5px;line-height:1.35}.node-inner p{font-size:11.5px;color:#6d7783;margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.node-catalog{margin-top:8px;font-size:10px;color:#c4471c;background:var(--orange-soft);border-radius:6px;padding:3px 5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.edge-label{position:absolute;transform:translate(-50%,-50%);border:1px solid #e3e7ed;background:#fff;border-radius:7px;padding:3px 5px;max-width:94px;text-align:center;font-size:9px;line-height:1.2;color:#6d7681}.toolbar{position:absolute;right:13px;top:13px;z-index:5;display:flex;align-items:center;background:#fff;border:1px solid #e4e8ed;border-radius:11px;padding:3px;box-shadow:0 6px 18px rgba(20,30,40,.09)}.toolbar button,.zoom{height:29px;border:0;background:transparent;border-radius:7px;color:#56616d;cursor:pointer}.toolbar button{width:29px;font-size:15px}.toolbar button:hover{background:#f1f3f5}.zoom{min-width:48px;display:grid;place-items:center;font-size:11px}.catalog-groups{display:grid;gap:17px}.catalog-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}.catalog-head h3{font-size:15px}.catalog-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.catalog-card{border:1px solid #e4e9ee;border-radius:13px;padding:13px;background:#fff}.catalog-card.selected{border-color:#ffcfbd;background:#fffaf8}.catalog-card h4{font-size:13px;margin:0 0 5px;line-height:1.35}.catalog-meta{color:#6f7885;font-size:11px;word-break:break-word}.catalog-card p{font-size:12px;color:#4d5762;line-height:1.5;margin:8px 0}.catalog-card a{font-size:12px;font-weight:750;color:#c6451b;text-decoration:none}.catalog-card a:hover{text-decoration:underline}.catalog-card .invalid{font-size:11px;color:var(--red)}details.more{margin-top:9px;border-top:1px dashed #dfe4e9;padding-top:8px}details.more summary{cursor:pointer;font-size:12px;color:#687280;font-weight:700}.roadmap{display:grid;gap:9px}.roadmap-item{display:grid;grid-template-columns:38px 1fr;gap:10px;padding:11px;border:1px solid #e9edf1;border-radius:12px}.phase{display:grid;place-items:center;border-radius:9px;background:var(--violet-soft);color:#654bd6;font-weight:800;font-size:12px}.roadmap-item h3{font-size:13px}.roadmap-item p{font-size:12px;color:#626c78;margin:4px 0}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.detail-card{padding:11px;border:1px solid #e9edf1;border-radius:11px}.detail-card h3{font-size:12px}.detail-card p{font-size:12px;white-space:pre-wrap;color:#59636f;margin:5px 0 0}.technical details{border-top:1px solid #eceff2;padding:11px 0}.technical summary{font-size:13px;font-weight:750;cursor:pointer}.technical pre{font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word;background:#f7f8fa;border:1px solid #e9edf1;border-radius:10px;padding:10px;color:#57616e}.drawer-backdrop{position:fixed;inset:0;background:rgba(17,24,31,.28);opacity:0;pointer-events:none;transition:opacity .16s;z-index:20}.drawer-backdrop.open{opacity:1;pointer-events:auto}.drawer{position:fixed;z-index:21;top:15px;right:15px;bottom:15px;width:min(470px,calc(100vw - 30px));background:#fff;border-radius:20px;box-shadow:0 22px 60px rgba(15,22,30,.22);transform:translateX(calc(100% + 25px));transition:transform .2s;overflow:auto}.drawer.open{transform:translateX(0)}.drawer-head{display:flex;justify-content:space-between;gap:10px;align-items:center;position:sticky;top:0;background:rgba(255,255,255,.96);backdrop-filter:blur(8px);border-bottom:1px solid #edf0f2;padding:18px}.close{border:0;background:#f0f2f4;width:31px;height:31px;border-radius:8px;font-size:18px;cursor:pointer}.drawer-body{padding:8px 18px 20px}.drawer-block{padding:13px 0;border-bottom:1px solid #edf0f2}.drawer-block h3{font-size:11px;color:#7e8792;margin:0 0 5px}.drawer-block div{font-size:13px;white-space:pre-wrap;word-break:break-word;color:#3c4550}.static-fallback{padding:20px 24px;border-top:1px solid var(--line);background:#fff}.static-fallback h2{font-size:16px}.static-fallback pre{white-space:pre-wrap;font:13px/1.6 inherit;color:#3c4550}.print-only{display:none}@media print{body{background:#fff}.shell{max-width:none;padding:0}.top,.toolbar,.tabs,.drawer,.drawer-backdrop{display:none!important}.graph-frame{height:auto;overflow:visible}.graph-viewport{overflow:visible}.graph-world{transform:none!important}.print-only{display:block}.card{box-shadow:none;break-inside:avoid}}@media(max-width:1000px){.catalog-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.summary-grid{grid-template-columns:1fr}.third{grid-column:span 6}}@media(max-width:720px){.shell{padding:16px 14px 48px}.hero-main{padding:22px 19px 17px}h1{font-size:23px}.hero-status{display:block}.status-card{border-right:0;border-bottom:1px solid var(--line)}.quick{padding:12px 16px}.half,.third{grid-column:span 12}.catalog-grid,.facts,.detail-grid{grid-template-columns:1fr}.graph-frame{height:410px}.section{padding:17px}.top{align-items:flex-start;flex-direction:column;gap:6px}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
"""


# Kept separate from the base stylesheet so the drawer can evolve without
# risking the fixed graph/canvas layout.  It deliberately presents only
# decision-useful business information; raw view-model objects stay out of the
# visible report.
DRAWER_CSS = r"""
.drawer{display:flex;flex-direction:column}.drawer-head{z-index:2}.drawer-head-copy{min-width:0}.drawer-kicker{margin:3px 0 0;color:var(--muted);font-size:12px}.drawer-body{display:grid;gap:0;align-content:start}.drawer-empty{margin:14px 0;color:var(--muted);font-size:13px}.drawer-block{padding:16px 0}.drawer-block:first-child{padding-top:17px}.drawer-block h3{font-size:12px;font-weight:800;color:#56616d;margin:0 0 7px}.drawer-block p{margin:0;color:#344050;font-size:13px;line-height:1.65;white-space:pre-wrap;word-break:break-word}.drawer-list{margin:0;padding-left:19px;color:#344050;font-size:13px;line-height:1.6}.drawer-list li{margin:5px 0}.drawer-catalog-list{display:grid;gap:10px}.drawer-catalog-card{padding:13px;border:1px solid #f0d8cf;border-radius:12px;background:#fffaf8}.drawer-catalog-card h4{margin:0 0 7px;color:#27303a;font-size:13px;line-height:1.45}.drawer-catalog-card p{font-size:12.5px;line-height:1.58}.drawer-catalog-label{display:block;margin:10px 0 4px;color:#7d4a37;font-size:11px;font-weight:800}.drawer-catalog-card .drawer-list{font-size:12.5px}.drawer-catalog-link{display:inline-flex;align-items:center;margin-top:11px;color:#bf431d;font-size:12px;font-weight:800;text-decoration:none}.drawer-catalog-link:hover{text-decoration:underline}.drawer-catalog-link:focus{outline:3px solid rgba(239,91,42,.28);outline-offset:3px;border-radius:3px}.drawer-catalog-note{margin:9px 0 0;color:var(--muted);font-size:12px}.drawer .close:focus{outline:3px solid rgba(114,87,232,.35);outline-offset:2px}
"""


GRAPH_LAYOUT_CSS = r"""
/* Deterministic left-to-right layout.  The world is fitted on first paint,
   while the original pan/zoom affordances remain available for inspection. */
.graph-frame{height:clamp(480px,56vw,640px);background:linear-gradient(180deg,#fcfdfe 0%,#f7f9fb 100%)}
.graph-viewport{touch-action:none;scrollbar-color:#c6ced8 transparent;scrollbar-width:thin}
.graph-viewport::-webkit-scrollbar{width:9px;height:9px}.graph-viewport::-webkit-scrollbar-thumb{background:#c6ced8;border:2px solid transparent;background-clip:padding-box;border-radius:99px}.graph-viewport::-webkit-scrollbar-track{background:transparent}
.edges{overflow:visible}.edge-path{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}.edge-path.control{stroke:#aeb8c5}.edge-path.branch{stroke:#bd842a}.edge-path.error{stroke:#c85b5b}.edge-path.human{stroke:#8068dc}
.edge-label{z-index:3;display:block;max-width:112px;padding:4px 7px;border-color:#dce3ea;background:rgba(255,255,255,.96);box-shadow:0 3px 10px rgba(24,34,44,.07);color:#5e6875;font-size:9.5px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:auto;cursor:help}
.flow-node{min-height:132px;transition:transform .14s ease,box-shadow .14s ease}.flow-node:hover{transform:translateY(-2px);box-shadow:0 12px 24px rgba(25,32,40,.12)}
.flow-node:focus-visible{outline:3px solid rgba(114,87,232,.42);outline-offset:3px}.flow-node .node-inner{min-height:128px}
.toolbar button:focus-visible,.toolbar .zoom:focus-visible{outline:3px solid rgba(114,87,232,.35);outline-offset:2px}.toolbar button[aria-label="전체 보기"]{font-size:14px}
@media(max-width:720px){.graph-frame{height:460px}.edge-label{max-width:104px;font-size:9px}}
"""


DRAWER_IO_CSS = r"""
/* Keep implementation guidance visually distinct from the port cards.  The
   generic .drawer-block p rule is intentionally overridden here so these
   notices retain their own vertical rhythm. */
.drawer-io-section{padding:18px 0 24px}.drawer-io-section+.drawer-block{padding-top:21px}.drawer-io-intro{margin:0 0 13px;color:#59636f;font-size:12.5px;line-height:1.68}.drawer-io-status{display:inline-flex;align-items:center;margin:0 0 17px;padding:5px 8px;border-radius:999px;background:var(--violet-soft);color:#5f43d5;font-size:11px;font-weight:800}.drawer-io-status.needs-check{background:var(--amber-soft);color:var(--amber)}.drawer-io-grid{display:grid;gap:16px}.drawer-io-card{padding:17px 16px;border:1px solid #e1e7ed;border-radius:14px;background:#fbfcfd}.drawer-io-card h4{margin:0;color:#27303a;font-size:13.5px;line-height:1.5}.drawer-port-meta{display:flex;gap:6px;flex-wrap:wrap;margin:9px 0 0}.drawer-port-type,.drawer-port-required,.drawer-port-optional{display:inline-flex;align-items:center;padding:4px 7px;border-radius:6px;font-size:10.5px;font-weight:780}.drawer-port-type{background:#eef2f6;color:#536170}.drawer-port-required{background:#fff0ea;color:#bf431d}.drawer-port-optional{background:#f2f3f5;color:#697482}.drawer-io-route{margin:14px 0 0;padding:13px 14px;border-left:3px solid #b9c2ce;border-radius:0 10px 10px 0;background:#fff;color:#3f4955;font-size:12.5px;line-height:1.72}.drawer-io-route-label{color:#59636f;font-size:11px}.drawer-stage-name{display:inline;padding:1px 4px;border-radius:5px;background:var(--violet-soft);color:#5c43cc;font-size:12.5px;font-weight:850;line-height:inherit;box-decoration-break:clone;-webkit-box-decoration-break:clone}.drawer-io-route .drawer-stage-name{margin:0 1px}.drawer-io-bindings{display:grid;gap:12px;margin-top:14px}.drawer-io-binding{padding:12px 13px;border:1px dashed #d5dee7;border-radius:10px;background:#fff;font-size:12.5px;line-height:1.72;color:#45515d}.drawer-io-empty{margin:14px 0 0;color:#737d89;font-size:12.5px;line-height:1.62}.drawer-io-notes{display:grid;gap:14px;margin-top:23px}.drawer-io-section .drawer-io-note{margin:0;padding:16px 17px;border:1px solid #f0d8cf;border-radius:12px;background:#fffaf8;color:#684937;font-size:12.5px;line-height:1.72}.drawer-io-section .drawer-io-note.needs-check{border-color:#f1d49e;background:#fffdf8;color:#76511a}.drawer-io-list{margin:10px 0 0;padding-left:19px;color:#45515d;font-size:12.5px;line-height:1.65}.drawer-io-list li{margin:5px 0}@media(max-width:480px){.drawer-io-section{padding-bottom:21px}.drawer-io-section+.drawer-block{padding-top:19px}.drawer-io-grid{gap:13px}.drawer-io-card{padding:15px 14px}.drawer-io-route,.drawer-io-binding{padding:11px 12px}.drawer-io-notes{gap:12px;margin-top:18px}.drawer-io-section .drawer-io-note{padding:14px 15px}}
"""


REFINEMENT_CSS = r"""
.refinement-content{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,.9fr);gap:14px;align-items:start}.refinement-message{margin:0;padding:14px 16px;border:1px solid #e5e9ee;border-left:4px solid var(--violet);border-radius:12px;background:#fbfbff;color:#344050;font-size:13px;line-height:1.7}.refinement-instruction{padding:14px 16px;border:1px solid #f0d8cf;border-radius:12px;background:#fffaf8}.refinement-instruction h3{margin:0 0 6px;color:#7d4a37;font-size:12px}.refinement-instruction p{margin:0;color:#4d5762;font-size:13px;line-height:1.65;white-space:pre-wrap;word-break:break-word}@media(max-width:720px){.refinement-content{grid-template-columns:1fr}}
"""


IMPLEMENTATION_SUMMARY_CSS = r"""
.implementation-summary-item{position:relative;min-height:118px;padding:15px 15px 14px 51px;border-color:#e7e4f7;background:linear-gradient(135deg,#fff 0%,#fbfaff 100%)}.implementation-summary-item h3{color:#27303a}.implementation-summary-item p{line-height:1.6}.summary-number{position:absolute;left:14px;top:14px;display:grid;place-items:center;width:26px;height:26px;border-radius:8px;background:var(--violet-soft);color:#6249d0;font-size:11px;font-weight:850}.implementation-summary-item:nth-child(2) .summary-number{background:var(--orange-soft);color:#c24218}.implementation-summary-item:nth-child(3) .summary-number{background:var(--green-soft);color:var(--green)}
"""


JS = r"""
(()=>{'use strict';const data=JSON.parse(document.getElementById('report-data').textContent);const $=s=>document.querySelector(s);const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e};const text=v=>typeof v==='string'?v:(v==null?'':String(v));const list=v=>Array.isArray(v)?v.filter(x=>text(x).trim()):[];const status=data.completion_status||{};$('#report-title').textContent=data.title||'업무 방식 및 개선 실행 보고서';$('#status-label').textContent=status.label||'설계 결과';$('#status-dot').className='status-dot '+(status.code==='COMPLETED'?'ok':'');$('#status-dot').style.background=status.code==='COMPLETED'?'var(--green)':'var(--amber)';[['보완 필요',status.information_gap_count||0],['검토 후보',status.catalog_candidate_count||0],['적용 권고',status.catalog_selected_count||0]].forEach(([a,b])=>{const s=node('span','',a+' ');const strong=node('b','',b);s.append(strong);$('#quick').append(s)});
const source=data.source_input||{};$('#source-description').textContent=source.description_display_redacted||'업무 설명이 제공되지 않았습니다.';if((source.redaction_count||0)>0)$('#source-note').textContent='민감정보 '+source.redaction_count+'건은 [REDACTED]로 마스킹되어 표시됩니다.';else $('#source-note').textContent='입력한 업무 설명을 요약으로 대체하지 않고 그대로 표시합니다.';
const refinement=data.refinement_summary&&typeof data.refinement_summary==='object'?data.refinement_summary:{};const requested=refinement.final_refinement_instructions_provided===true||text(refinement.final_refinement_instructions).trim()!=='';const rawRefinementStatus=text(refinement.status).trim().toUpperCase();const refinementStatus=['APPLIED','SKIPPED','NONE'].includes(rawRefinementStatus)?rawRefinementStatus:'SKIPPED';const refinementHasResult=requested||refinement.status_provided===true||rawRefinementStatus!=='';if(refinementHasResult){const section=$('#refinement-section');const host=$('#refinement-content');const badgeHost=$('#refinement-status');const labels={APPLIED:'보완 반영 완료',SKIPPED:'기본 초안 사용',NONE:'기본 초안 사용'};const badgeClass={APPLIED:'green',SKIPPED:'amber',NONE:'violet'};const fallbackCopy={APPLIED:'초안 점검과 보완 지시를 반영해 최종 설계를 한 번 더 다듬었습니다.',SKIPPED:'2차 보완 결과를 적용하지 못해 검증된 기본 초안을 기준으로 보고서를 작성했습니다.',NONE:requested?'보완 지시는 제공됐지만 2차 보완 결과가 없어 기본 초안을 기준으로 보고서를 작성했습니다.':'2차 보완 단계는 요청되지 않아 기본 초안을 기준으로 보고서를 작성했습니다.'};section.hidden=false;badgeHost.append(node('span','badge '+badgeClass[refinementStatus],labels[refinementStatus]));host.append(node('p','refinement-message',text(refinement.summary).trim()||fallbackCopy[refinementStatus]));const instruction=text(refinement.final_refinement_instructions).trim();if(instruction){const box=node('div','refinement-instruction');box.append(node('h3','', '요청한 보완 방향'),node('p','',instruction));host.append(box)}}
const blocks=data.business_report||{};
// The top of the report is intentionally an implementation brief, not a
// duplicate of the analysis, catalog, roadmap, risks, and gaps shown below.
const toBeGraph=data.to_be_graph&&typeof data.to_be_graph==='object'?data.to_be_graph:{};
const toBeNodes=Array.isArray(toBeGraph.nodes)?toBeGraph.nodes:[];
const relevantNodes=toBeNodes.filter(n=>n&&typeof n==='object'&&!['start','end'].includes(n.node_kind));
const selectedCatalog=(data.catalog_application_plan&&Array.isArray(data.catalog_application_plan.selected))?data.catalog_application_plan.selected:[];
const newCustomCount=relevantNodes.filter(n=>n.implementation_source==='new_component').length;
const externalServiceCount=relevantNodes.filter(n=>n.implementation_source==='external_service').length;
const humanNodes=relevantNodes.filter(n=>n.node_kind==='human_review'||n.implementation_source==='human_task');
const exceptionNodes=relevantNodes.filter(n=>n.node_kind==='exception');
const conciseNames=(names,limit=3)=>{const visible=names.slice(0,limit);return visible.join(' → ')+(names.length>visible.length?' 등':'')};
const automationCopy=text(blocks.executive_summary?.summary).trim()
  || text(blocks.improvement_direction?.summary).trim()
  || '반복 업무를 Agent가 처리하고, 사람 판단이 필요한 단계는 분리합니다.';
const buildParts=[];
if(selectedCatalog.length)buildParts.push('카탈로그 자산 '+selectedCatalog.length+'개 재사용 후보');
if(newCustomCount)buildParts.push('신규 Custom '+newCustomCount+'개');
if(externalServiceCount)buildParts.push('외부 연동 '+externalServiceCount+'개');
const buildCopy=buildParts.length
  ? buildParts.join(' + ')+'로 조립하고, 실제 연결 전 포트·권한을 확인합니다.'
  : '카탈로그 자산은 후보로만 검토하고, 필요한 기능은 신규 Custom 또는 외부 연동으로 구현합니다.';
const reviewNames=humanNodes.map(n=>text(n.title).trim()).filter(Boolean);
const safetyParts=[];
if(reviewNames.length)safetyParts.push(conciseNames(reviewNames)+'에서 사람 검토·승인');
if(exceptionNodes.length)safetyParts.push('오류·예외 '+exceptionNodes.length+'개 경로는 자동 차단·안내');
const safetyCopy=safetyParts.length
  ? safetyParts.join(', ')+'하도록 운영 경계를 둡니다.'
  : '사람 검토와 오류 차단 기준은 실제 구현 전에 업무 설명과 함께 확정합니다.';
[
  ['01', 'Agent가 자동으로 처리할 일', automationCopy],
  ['02', 'Agent를 조립하는 방식', buildCopy],
  ['03', '사람 검토와 차단 경계', safetyCopy],
].forEach(([number,label,copy])=>{const article=node('article','summary-item implementation-summary-item');const marker=node('span','summary-number',number);article.append(marker,node('h3','',label),node('p','',copy));$('#summary-grid').append(article)});
const gapHost=$('#gaps');const severity={required:['필수 보완','required'],important:['중요 보완','important'],optional:['선택 보완','optional']};const gaps=Array.isArray(data.information_gaps)?data.information_gaps:[];if(!gaps.length){gapHost.append(node('p','note','현재 확인된 추가 보완 항목이 없습니다. 실제 구현 전에는 권한과 데이터 계약을 다시 확인하세요.'))}else gaps.forEach(g=>{const kind=severity[g.severity]||severity.important;const box=node('article','gap '+kind[1]);const b=node('span','badge '+(kind[1]==='required'?'red':kind[1]==='optional'?'violet':'amber'),kind[0]);box.append(b,node('h3','',g.question||'추가 정보 확인 필요'));[['필요한 이유',g.why_needed],['현재 설계 영향',g.design_impact]].forEach(([label,value])=>{if(text(value).trim())box.append(node('p','',label+' · '+value))});if(text(g.suggested_description_text).trim()){const c=node('div','copy-box',g.suggested_description_text);c.setAttribute('aria-label','다음 실행에서 추가할 문장 예시');box.append(c)}gapHost.append(box)});
const context=$('#context');const contextRows=[['업무 목적',(data.business_report?.work_overview?.summary)||'확인 필요'],['업무 범위',((blocks.work_overview||{}).facts||[]).map(f=>f.value).join(' · ')||'확인 필요'],['현재 문제',((blocks.as_is_analysis||{}).bullets||[]).join(' · ')||'확인 필요'],['개선 원칙',((blocks.improvement_direction||{}).bullets||[]).join(' · ')||'확인 필요']];contextRows.forEach(([a,b])=>{const d=node('div','detail-card');d.append(node('h3','',a),node('p','',b));context.append(d)});
const sourceLabels={human_task:'사람 수행',builtin:'기본 요소',catalog_component:'기존 Component',catalog_flow:'기존 Flow',new_component:'신규 Custom',external_service:'외부 서비스'};function catalogUrl(item){if(!item||typeof item!=='object')return null;const id=text(item.asset_id).toLowerCase(),kind=item.asset_type==='flow'?'flow':'component',url=text(item.catalog_url);const pattern=new RegExp('^https://agent-hub\\.skhynix\\.com/#/'+kind+'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$','i');return pattern.test(url)&&url.toLowerCase().endsWith('/'+id)?url:null}function openDrawer(title,fields){$('#drawer-title').textContent=title;const host=$('#drawer-body');host.replaceChildren();Object.entries(fields||{}).forEach(([label,value])=>{if(value==null||value===''||(Array.isArray(value)&&!value.length))return;const block=node('section','drawer-block');block.append(node('h3','',label));const val=node('div','');if(Array.isArray(value))val.textContent=value.map(v=>typeof v==='string'?'• '+v:'• '+JSON.stringify(v)).join('\n');else if(typeof value==='object')val.textContent=JSON.stringify(value,null,2);else val.textContent=text(value);block.append(val);host.append(block)});$('#drawer').classList.add('open');$('#backdrop').classList.add('open')}function closeDrawer(){$('#drawer').classList.remove('open');$('#backdrop').classList.remove('open')}$('#close').addEventListener('click',closeDrawer);$('#backdrop').addEventListener('click',closeDrawer);document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer()});
function graphLayout(graph){const nodes=Array.isArray(graph.nodes)?[...graph.nodes].sort((a,b)=>(a.sequence||0)-(b.sequence||0)):[];const edges=Array.isArray(graph.edges)?graph.edges:[];const rows=new Map();nodes.forEach((n,i)=>rows.set(n.node_id,n.node_kind==='exception'?2:0));edges.filter(e=>e.edge_kind==='branch'||e.edge_kind==='error').forEach((e,i)=>{if(rows.has(e.target_node_id))rows.set(e.target_node_id,1+(i%2))});const positions=new Map();nodes.forEach((n,i)=>positions.set(n.node_id,{x:54+i*260,y:64+(rows.get(n.node_id)||0)*152}));return {nodes,edges,positions,width:Math.max(420,72+nodes.length*260),height:Math.max(300,120+(Math.max(0,...[...rows.values()])*152)+150)}}function renderGraph(graph){const host=$('#graph-host');host.replaceChildren();const frame=node('div','graph-frame');const vp=node('div','graph-viewport'),world=node('div','graph-world'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('edges');const layer=node('div','node-layer');world.append(svg,layer);vp.append(world);frame.append(vp);const bar=node('div','toolbar'),minus=node('button','', '−'),read=node('div','zoom',''),plus=node('button','', '+'),fit=node('button','', '↙');fit.title='전체 보기';bar.append(minus,read,plus,fit);frame.append(bar);host.append(frame);const layout=graphLayout(graph);world.style.width=layout.width+'px';world.style.height=layout.height+'px';svg.setAttribute('width',layout.width);svg.setAttribute('height',layout.height);let scale=1;function apply(){world.style.transform='scale('+scale+')';read.textContent=Math.round(scale*100)+'%'}function fitAll(){scale=Math.max(.38,Math.min(1,(vp.clientWidth-34)/layout.width,(vp.clientHeight-34)/layout.height));apply();vp.scrollTo({left:0,top:0})}function zoom(delta){scale=Math.max(.3,Math.min(1.6,Math.round((scale+delta)*100)/100));apply()}minus.addEventListener('click',()=>zoom(-.1));plus.addEventListener('click',()=>zoom(.1));fit.addEventListener('click',fitAll);window.addEventListener('resize',fitAll,{passive:true});const defs=document.createElementNS('http://www.w3.org/2000/svg','defs'),mark=document.createElementNS('http://www.w3.org/2000/svg','marker');mark.setAttribute('id','arrow');mark.setAttribute('viewBox','0 0 10 10');mark.setAttribute('refX','9');mark.setAttribute('refY','5');mark.setAttribute('markerWidth','6');mark.setAttribute('markerHeight','6');mark.setAttribute('orient','auto');const mp=document.createElementNS('http://www.w3.org/2000/svg','path');mp.setAttribute('d','M0,0 L10,5 L0,10 z');mp.setAttribute('fill','#b5bdc8');mark.append(mp);defs.append(mark);svg.append(defs);layout.edges.forEach(edge=>{const s=layout.positions.get(edge.source_node_id),t=layout.positions.get(edge.target_node_id);if(!s||!t)return;const sx=s.x+208,sy=s.y+65,tx=t.x,ty=t.y+65,mid=(sx+tx)/2;const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M '+sx+' '+sy+' C '+(mid)+' '+sy+', '+(mid)+' '+ty+', '+tx+' '+ty);path.setAttribute('fill','none');path.setAttribute('stroke',edge.edge_kind==='error'?'#d66b6b':edge.edge_kind==='branch'?'#c28b30':'#b5bdc8');path.setAttribute('stroke-width','2');path.setAttribute('marker-end','url(#arrow)');svg.append(path);const label=node('div','edge-label',edge.label||'다음');label.style.left=mid+'px';label.style.top=((sy+ty)/2)+'px';layer.append(label)});layout.nodes.forEach(n=>{const pos=layout.positions.get(n.node_id),btn=node('button','flow-node '+(n.node_kind||''));btn.type='button';btn.style.left=pos.x+'px';btn.style.top=pos.y+'px';btn.append(node('div','stripe'));const inner=node('div','node-inner');const meta=node('div','node-meta');meta.append(node('span','',sourceLabels[n.implementation_source]||'업무 단계'),node('span','', 'STEP '+((n.sequence||0)+1)));inner.append(meta,node('h3','',n.title||'업무 단계'),node('p','',n.summary||''));const refs=Array.isArray(n.catalog_refs)?n.catalog_refs:[];if(refs.length){const plan=(data.catalog_application_plan?.selected||[]);const names=refs.map(r=>plan.find(a=>a.asset_id===r.asset_id&&a.version===r.version)?.title).filter(Boolean);inner.append(node('div','node-catalog','카탈로그 · '+names.join(', ')))}btn.append(inner);btn.addEventListener('click',()=>openDrawer(n.title||'업무 단계',graph.details?.[n.detail_ref]||{설명:n.summary||''}));layer.append(btn)});apply();requestAnimationFrame(fitAll);let dragging=false,startX=0,startY=0,scrollX=0,scrollY=0;vp.addEventListener('pointerdown',e=>{if(e.target.closest('button'))return;dragging=true;startX=e.clientX;startY=e.clientY;scrollX=vp.scrollLeft;scrollY=vp.scrollTop;vp.setPointerCapture(e.pointerId)});vp.addEventListener('pointermove',e=>{if(dragging){vp.scrollLeft=scrollX-(e.clientX-startX);vp.scrollTop=scrollY-(e.clientY-startY)}});vp.addEventListener('pointerup',()=>dragging=false);vp.addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();zoom(e.deltaY<0?.1:-.1)}},{passive:false})}
/* Graph rendering is supplied by GRAPH_LAYOUT_JS below.  The legacy helper
   functions stay in this compatibility bundle only for old cached reports;
   they are deliberately not invoked for newly rendered reports. */
function statusText(value){return ({verified_runtime:'실행 검증 이력 있음',ports_extracted:'포트 계약 확인 필요',flow_graph_extracted:'Flow 구조 확인됨',metadata_only:'설명 기반 검토 후보',unknown:'상세 확인 필요'})[value]||'상세 확인 필요'}function catalogCard(item,selected){const card=node('article','catalog-card '+(selected?'selected':''));card.append(node('h4','',item.title||'카탈로그 자산'));card.append(node('div','catalog-meta',(item.asset_type==='flow'?'Flow':'Component')+' · '+(item.version||'unknown')));card.append(node('div','catalog-meta',item.asset_id||''));const badge=node('span','badge '+(selected?'orange':'violet'),statusText(item.technical_contract_status));card.append(badge);if(text(item.reason).trim())card.append(node('p','',item.reason));if(Array.isArray(item.target_node_ids)&&item.target_node_ids.length)card.append(node('p','', '적용 위치 · '+item.target_node_ids.join(', ')));const href=catalogUrl(item);if(href){const a=node('a','', 'Agent Hub 상세 보기');a.href=href;a.target='_blank';a.rel='noopener noreferrer';card.append(a)}else card.append(node('div','invalid','Agent Hub 링크 검증 실패'));return card}const plans=data.catalog_application_plan||{};[['selected','적용 권고','이 단계에 직접 적용을 권고하는 후보입니다.',true],['considered','연결 검토 후보','관련성은 있으나 포트·권한·실행 조건을 확인해야 합니다.',false],['not_used','사용하지 않은 검색 후보','검색 후보였지만 이번 설계에는 적용하지 않았습니다.',false]].forEach(([key,title,copy,sel])=>{const items=Array.isArray(plans[key])?plans[key]:[];const sec=node('section','');const head=node('div','catalog-head');head.append(node('h3','',title),node('span','badge '+(sel?'orange':'violet'),items.length+'개'));sec.append(head,node('p','note',copy));const grid=node('div','catalog-grid');const visible=key==='not_used'?items.slice(0,6):items;visible.forEach(item=>grid.append(catalogCard(item,sel)));sec.append(grid);if(key==='not_used'&&items.length>visible.length){const more=node('details','more'),summary=node('summary','', '나머지 '+(items.length-visible.length)+'개 검색 후보 보기'),moreGrid=node('div','catalog-grid');items.slice(visible.length).forEach(item=>moreGrid.append(catalogCard(item,false)));more.append(summary,moreGrid);sec.append(more)}$('#catalog-groups').append(sec)});
const road=$('#roadmap');const impl=Array.isArray(data.implementation_plan)?data.implementation_plan:[];if(!impl.length)road.append(node('p','note','구현 로드맵은 업무 설명의 보완 항목을 반영한 뒤 구체화하세요.'));impl.forEach((item,i)=>{const row=node('article','roadmap-item');row.append(node('div','phase',''+(item.phase||i+1)));const body=node('div','');body.append(node('h3','',item.title||'구현 단계'));const bits=[];if(list(item.actions).length)bits.push('실행 · '+list(item.actions).join(' · '));if(list(item.dependencies).length)bits.push('선행 · '+list(item.dependencies).join(' · '));if(list(item.completion_criteria).length)bits.push('완료 기준 · '+list(item.completion_criteria).join(' · '));body.append(node('p','',bits.join('\n')));row.append(body);road.append(row)});
const risk=$('#risks'),tests=$('#tests');const two=(host,items,kind)=>{if(!items.length){host.append(node('p','note','현재 추가 항목이 없습니다.'));return}items.forEach(item=>{const d=node('article','detail-card');const title=kind==='risk'?item.risk:item.title;const body=kind==='risk'?('영향 · '+text(item.impact)+'\n통제 · '+text(item.control)+'\n담당 · '+text(item.owner_role)):('Given · '+text(item.given)+'\nWhen · '+text(item.when)+'\nThen · '+text(item.then));d.append(node('h3','',title||'확인 항목'),node('p','',body));host.append(d)})};two(risk,Array.isArray(data.risks_and_controls)?data.risks_and_controls:[],'risk');two(tests,Array.isArray(data.validation_plan)?data.validation_plan:[],'test');const trace=$('#trace');trace.textContent=JSON.stringify(data.technical_trace||{},null,2);})();
"""


GRAPH_LAYOUT_JS = r"""
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("report-data").textContent);
  const host = document.getElementById("graph-host");
  const nodeWidth = 208;
  const nodeHeight = 132;
  const layerGap = 118;
  const rowGap = 44;
  const sidePadding = 54;
  let renderNumber = 0;

  const text = (value) => (typeof value === "string" ? value : (value == null ? "" : String(value)));
  const cleanText = (value) => text(value).replace(/\s+/g, " ").trim();
  const element = (tag, className, value) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (value !== undefined) result.textContent = String(value);
    return result;
  };
  const sourceLabels = {
    human_task: "사람 수행",
    builtin: "기본 기능",
    catalog_component: "기존 Component",
    catalog_flow: "기존 Flow",
    new_component: "신규 Custom",
    external_service: "외부 서비스",
  };
  const edgeClass = (kind) => ({ branch: "branch", error: "error", human: "human" })[kind] || "control";
  const edgeLabelMinWidth = 52;
  const edgeLabelMaxWidth = 112;
  const edgeLabelHeight = 24;
  const edgeLabelWidth = (label) => {
    // A Korean glyph is materially wider than an ASCII character in the
    // report font.  This deterministic estimate gives ordinary Korean labels
    // a one-line pill without needing browser-only measurement APIs.
    const units = [...cleanText(label)].reduce((total, character) => {
      if (/\s/.test(character)) return total + 0.55;
      return total + (/[^\x00-\xff]/.test(character) ? 1.65 : 1);
    }, 0);
    return Math.min(edgeLabelMaxWidth, Math.max(edgeLabelMinWidth, Math.ceil(units * 6 + 16)));
  };
  const stableNodeOrder = (left, right) => {
    const leftSequence = Number.isFinite(Number(left?.sequence)) ? Number(left.sequence) : Number.MAX_SAFE_INTEGER;
    const rightSequence = Number.isFinite(Number(right?.sequence)) ? Number(right.sequence) : Number.MAX_SAFE_INTEGER;
    return leftSequence - rightSequence
      || cleanText(left?.node_id).localeCompare(cleanText(right?.node_id), "ko")
      || cleanText(left?.title).localeCompare(cleanText(right?.title), "ko");
  };

  function buildLayout(graph) {
    const nodes = (Array.isArray(graph?.nodes) ? graph.nodes : [])
      .filter((item) => item && typeof item === "object" && cleanText(item.node_id))
      .slice()
      .sort(stableNodeOrder);
    const nodeById = new Map(nodes.map((item) => [item.node_id, item]));
    const sequenceIndex = new Map(nodes.map((item, index) => [item.node_id, index]));
    const edges = (Array.isArray(graph?.edges) ? graph.edges : [])
      .filter((edge) => edge && nodeById.has(edge.source_node_id) && nodeById.has(edge.target_node_id))
      .map((edge, index) => ({ ...edge, _stableIndex: index }))
      .sort((left, right) => {
        const source = (sequenceIndex.get(left.source_node_id) || 0) - (sequenceIndex.get(right.source_node_id) || 0);
        const target = (sequenceIndex.get(left.target_node_id) || 0) - (sequenceIndex.get(right.target_node_id) || 0);
        return source || target || cleanText(left.edge_id).localeCompare(cleanText(right.edge_id), "ko") || left._stableIndex - right._stableIndex;
      });
    const incoming = new Map(nodes.map((item) => [item.node_id, []]));
    const outgoing = new Map(nodes.map((item) => [item.node_id, []]));
    edges.forEach((edge) => {
      incoming.get(edge.target_node_id).push(edge);
      outgoing.get(edge.source_node_id).push(edge);
    });

    // A stable longest-path layer is enough for business Flow diagrams and is
    // deterministic even if the model returns nodes in a different order.
    // Back edges are kept as lower return lanes instead of perturbing layers.
    const rank = new Map(nodes.map((item) => [item.node_id, 0]));
    nodes.forEach((item) => {
      const ownIndex = sequenceIndex.get(item.node_id);
      const forwardParents = incoming.get(item.node_id).filter(
        (edge) => (sequenceIndex.get(edge.source_node_id) || 0) < ownIndex,
      );
      if (forwardParents.length) {
        rank.set(item.node_id, Math.max(...forwardParents.map((edge) => (rank.get(edge.source_node_id) || 0) + 1)));
      }
    });
    const layers = new Map();
    nodes.forEach((item) => {
      const layer = rank.get(item.node_id) || 0;
      if (!layers.has(layer)) layers.set(layer, []);
      layers.get(layer).push(item);
    });
    const layerNumbers = [...layers.keys()].sort((left, right) => left - right);
    const orderedLayers = new Map(layerNumbers.map((layer) => [layer, layers.get(layer).slice().sort(stableNodeOrder)]));

    // Two small barycentric sweeps group related branches together.  This is
    // deliberately bounded: the same view model always produces the same
    // positions, and no force-layout jitter can make a report change on reload.
    const orderMap = () => {
      const values = new Map();
      layerNumbers.forEach((layer) => (orderedLayers.get(layer) || []).forEach((item, index) => values.set(item.node_id, index)));
      return values;
    };
    for (let pass = 0; pass < 3; pass += 1) {
      let order = orderMap();
      layerNumbers.slice(1).forEach((layer) => {
        const previous = orderedLayers.get(layer).slice();
        orderedLayers.set(layer, previous.sort((left, right) => {
          const barycenter = (item) => {
            const parents = incoming.get(item.node_id).filter((edge) => (rank.get(edge.source_node_id) || 0) < layer);
            if (!parents.length) return Number.MAX_SAFE_INTEGER;
            return parents.reduce((total, edge) => total + (order.get(edge.source_node_id) || 0), 0) / parents.length;
          };
          return barycenter(left) - barycenter(right) || stableNodeOrder(left, right);
        }));
      });
      order = orderMap();
      layerNumbers.slice(0, -1).reverse().forEach((layer) => {
        const current = orderedLayers.get(layer).slice();
        orderedLayers.set(layer, current.sort((left, right) => {
          const barycenter = (item) => {
            const children = outgoing.get(item.node_id).filter((edge) => (rank.get(edge.target_node_id) || 0) > layer);
            if (!children.length) return Number.MAX_SAFE_INTEGER;
            return children.reduce((total, edge) => total + (order.get(edge.target_node_id) || 0), 0) / children.length;
          };
          return barycenter(left) - barycenter(right) || stableNodeOrder(left, right);
        }));
      });
    }

    const longForward = edges.filter((edge) => (rank.get(edge.target_node_id) || 0) > (rank.get(edge.source_node_id) || 0) + 1);
    const returnEdges = edges.filter((edge) => (rank.get(edge.target_node_id) || 0) < (rank.get(edge.source_node_id) || 0));
    // Same-layer links can be upward or downward after barycentric ordering.
    // Reserve lower lanes for all of them so an upward exception/feedback path
    // never shares a line with a primary execution edge.
    const sameLayerEdges = edges.filter((edge) => (rank.get(edge.target_node_id) || 0) === (rank.get(edge.source_node_id) || 0));
    const lowerRailEdges = returnEdges.concat(sameLayerEdges);
    const topRail = 48 + longForward.length * 16;
    const bottomRail = 48 + lowerRailEdges.length * 16;
    const maxRows = Math.max(1, ...layerNumbers.map((layer) => (orderedLayers.get(layer) || []).length));
    const nodeAreaHeight = maxRows * nodeHeight + Math.max(0, maxRows - 1) * rowGap;
    const width = Math.max(440, sidePadding * 2 + (layerNumbers.length || 1) * nodeWidth + Math.max(0, layerNumbers.length - 1) * layerGap);
    const height = Math.max(320, topRail + nodeAreaHeight + bottomRail);
    const positions = new Map();
    layerNumbers.forEach((layer, layerIndex) => {
      const group = orderedLayers.get(layer) || [];
      const groupHeight = group.length * nodeHeight + Math.max(0, group.length - 1) * rowGap;
      const startY = topRail + Math.max(0, (nodeAreaHeight - groupHeight) / 2);
      group.forEach((item, row) => positions.set(item.node_id, {
        x: sidePadding + layerIndex * (nodeWidth + layerGap),
        y: startY + row * (nodeHeight + rowGap),
        layer,
        layerIndex,
        row,
      }));
    });

    return {
      nodes,
      edges,
      positions,
      rank,
      width,
      height,
      topRail,
      bottomRail,
      adjacentGroups: new Map(),
      longForward,
      returnEdges,
      lowerRailEdges,
    };
  }

  function allocateLanes(layout) {
    layout.edges.forEach((edge) => {
      const source = layout.positions.get(edge.source_node_id);
      const target = layout.positions.get(edge.target_node_id);
      if (!source || !target) return;
      if (target.layer === source.layer + 1) {
        const key = `${source.layer}:${target.layer}`;
        if (!layout.adjacentGroups.has(key)) layout.adjacentGroups.set(key, []);
        layout.adjacentGroups.get(key).push(edge);
      }
    });
    layout.adjacentGroups.forEach((edges) => edges.sort((left, right) => {
      const leftSource = layout.positions.get(left.source_node_id);
      const rightSource = layout.positions.get(right.source_node_id);
      const leftTarget = layout.positions.get(left.target_node_id);
      const rightTarget = layout.positions.get(right.target_node_id);
      return leftSource.y - rightSource.y || leftTarget.y - rightTarget.y || left._stableIndex - right._stableIndex;
    }));
    layout.longForward.sort((left, right) => left._stableIndex - right._stableIndex);
    layout.returnEdges.sort((left, right) => left._stableIndex - right._stableIndex);
    layout.lowerRailEdges.sort((left, right) => left._stableIndex - right._stableIndex);
  }

  function routeForEdge(layout, edge) {
    const source = layout.positions.get(edge.source_node_id);
    const target = layout.positions.get(edge.target_node_id);
    if (!source || !target) return null;
    const sourceMidX = source.x + nodeWidth / 2;
    const sourceMidY = source.y + nodeHeight / 2;
    const targetMidX = target.x + nodeWidth / 2;
    const targetMidY = target.y + nodeHeight / 2;
    const sourceRight = source.x + nodeWidth;
    const targetLeft = target.x;
    const forwardDistance = target.layer - source.layer;
    const label = cleanText(edge.label) || cleanText(edge.condition) || "다음 단계";

    if (forwardDistance === 1) {
      const key = `${source.layer}:${target.layer}`;
      const siblings = layout.adjacentGroups.get(key) || [edge];
      const laneIndex = Math.max(0, siblings.indexOf(edge));
      if (Math.abs(sourceMidY - targetMidY) < 2) {
        return {
          d: `M ${sourceRight} ${sourceMidY} H ${targetLeft}`,
          label,
          labelX: (sourceRight + targetLeft) / 2,
          labelY: sourceMidY - 14,
        };
      }
      const gap = Math.max(24, targetLeft - sourceRight);
      const laneX = sourceRight + gap * ((laneIndex + 1) / (siblings.length + 1));
      return {
        d: `M ${sourceRight} ${sourceMidY} H ${laneX} V ${targetMidY} H ${targetLeft}`,
        label,
        labelX: laneX,
        labelY: (sourceMidY + targetMidY) / 2,
      };
    }

    if (forwardDistance > 1) {
      const laneIndex = Math.max(0, layout.longForward.indexOf(edge));
      const laneY = 28 + laneIndex * 16;
      return {
        d: `M ${sourceMidX} ${source.y} V ${laneY} H ${targetMidX} V ${target.y}`,
        label,
        labelX: (sourceMidX + targetMidX) / 2,
        labelY: laneY - 12,
      };
    }

    if (forwardDistance === 0 && source.y < target.y) {
      const sameLayerGap = Math.max(12, target.y - (source.y + nodeHeight));
      const laneY = source.y + nodeHeight + Math.min(sameLayerGap / 2, 20);
      return {
        d: `M ${sourceMidX} ${source.y + nodeHeight} V ${laneY} H ${targetMidX} V ${target.y}`,
        label,
        labelX: (sourceMidX + targetMidX) / 2,
        labelY: laneY - 12,
      };
    }

    // Backward links and same-layer upward links stay beneath the content so
    // they do not cross the primary left-to-right execution route.
    const laneIndex = Math.max(0, layout.lowerRailEdges.indexOf(edge));
    const laneY = layout.height - 28 - Math.max(0, laneIndex) * 16;
    return {
      d: `M ${sourceMidX} ${source.y + nodeHeight} V ${laneY} H ${targetMidX} V ${target.y + nodeHeight}`,
      label,
      labelX: (sourceMidX + targetMidX) / 2,
      labelY: laneY + 12,
    };
  }

  function renderGraph(graph) {
    if (!host) return;
    if (typeof host._graphCleanup === "function") host._graphCleanup();
    host.replaceChildren();
    const layout = buildLayout(graph);
    allocateLanes(layout);
    const frame = element("div", "graph-frame");
    frame.dataset.nodeCount = String(layout.nodes.length);
    const viewport = element("div", "graph-viewport");
    const world = element("div", "graph-world");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("edges");
    svg.setAttribute("aria-hidden", "true");
    const nodeLayer = element("div", "node-layer");
    world.append(svg, nodeLayer);
    viewport.append(world);
    frame.append(viewport);
    const toolbar = element("div", "toolbar");
    const minus = element("button", "", "−");
    const zoomReadout = element("div", "zoom", "");
    const plus = element("button", "", "+");
    const fit = element("button", "", "↙");
    minus.type = plus.type = fit.type = "button";
    minus.setAttribute("aria-label", "축소");
    plus.setAttribute("aria-label", "확대");
    fit.setAttribute("aria-label", "전체 보기");
    fit.title = "전체 보기";
    toolbar.append(minus, zoomReadout, plus, fit);
    frame.append(toolbar);
    host.append(frame);

    world.style.width = `${layout.width}px`;
    world.style.height = `${layout.height}px`;
    svg.setAttribute("width", String(layout.width));
    svg.setAttribute("height", String(layout.height));
    svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);

    const markerId = `orthogonal-arrow-${++renderNumber}`;
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", markerId);
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "6");
    marker.setAttribute("markerHeight", "6");
    marker.setAttribute("orient", "auto");
    const markerPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    markerPath.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    markerPath.setAttribute("fill", "#aeb8c5");
    marker.append(markerPath);
    defs.append(marker);
    svg.append(defs);

    const labelBoxes = [];
    const placeLabel = (route) => {
      const width = edgeLabelWidth(route.label);
      let x = route.labelX;
      let y = route.labelY;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        const overlaps = labelBoxes.some((box) => (
          Math.abs(box.x - x) < (box.width + width) / 2 + 6
          && Math.abs(box.y - y) < (box.height + edgeLabelHeight) / 2 + 6
        ));
        if (!overlaps) break;
        // Alternate above and below the original route rather than stacking
        // successive labels in the same direction.  It keeps neighbouring
        // branch labels legible without changing the deterministic routes.
        const step = Math.floor(attempt / 2) + 1;
        y = route.labelY + (attempt % 2 === 0 ? -1 : 1) * step * 24;
      }
      labelBoxes.push({ x, y, width, height: edgeLabelHeight });
      return { x, y, width };
    };
    layout.edges.forEach((edge) => {
      const route = routeForEdge(layout, edge);
      if (!route) return;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", route.d);
      path.setAttribute("class", `edge-path ${edgeClass(cleanText(edge.edge_kind))}`);
      path.setAttribute("stroke-width", cleanText(edge.edge_kind) === "branch" ? "2.25" : "2");
      path.setAttribute("marker-end", `url(#${markerId})`);
      svg.append(path);
      const point = placeLabel(route);
      const label = element("div", "edge-label", route.label);
      // The visible label is deliberately single-line.  The unabridged text
      // remains available to both pointer users and assistive technology when
      // a long branch name is ellipsized to fit safely between nodes.
      label.title = route.label;
      label.setAttribute("role", "note");
      label.setAttribute("aria-label", route.label);
      label.style.left = `${point.x}px`;
      label.style.top = `${point.y}px`;
      label.style.width = `${point.width}px`;
      nodeLayer.append(label);
    });
    layout.nodes.forEach((item) => {
      const position = layout.positions.get(item.node_id);
      if (!position) return;
      const button = element("button", `flow-node ${cleanText(item.node_kind) || "work_step"}`);
      button.type = "button";
      button.style.left = `${position.x}px`;
      button.style.top = `${position.y}px`;
      button.setAttribute("aria-label", [
        cleanText(item.title) || "업무 단계",
        sourceLabels[cleanText(item.implementation_source)] || "업무 단계",
        cleanText(item.summary),
      ].filter(Boolean).join(". "));
      button.append(element("div", "stripe"));
      const body = element("div", "node-inner");
      const meta = element("div", "node-meta");
      meta.append(
        element("span", "", sourceLabels[cleanText(item.implementation_source)] || "업무 단계"),
        element("span", "", `STEP ${(Number(item.sequence) || 0) + 1}`),
      );
      body.append(meta, element("h3", "", cleanText(item.title) || "업무 단계"), element("p", "", cleanText(item.summary)));
      const refs = Array.isArray(item.catalog_refs) ? item.catalog_refs : [];
      if (refs.length) {
        const selected = Array.isArray(data.catalog_application_plan?.selected) ? data.catalog_application_plan.selected : [];
        const titles = refs.map((ref) => selected.find((asset) => asset.asset_id === ref.asset_id && asset.version === ref.version)?.title).filter(Boolean);
        if (titles.length) body.append(element("div", "node-catalog", `카탈로그 · ${titles.join(", ")}`));
      }
      button.append(body);
      nodeLayer.append(button);
    });

    let scale = 1;
    const apply = () => {
      world.style.transform = `scale(${scale})`;
      zoomReadout.textContent = `${Math.round(scale * 100)}%`;
    };
    const fitAll = () => {
      const usableWidth = Math.max(1, viewport.clientWidth - 32);
      const usableHeight = Math.max(1, viewport.clientHeight - 32);
      const fitted = Math.min(1, usableWidth / layout.width, usableHeight / layout.height);
      // Do not impose a readability floor here: an initial overview must show
      // every node.  Users can immediately zoom in and pan for large graphs.
      scale = Number.isFinite(fitted) && fitted > 0 ? fitted : 1;
      apply();
      viewport.scrollTo({ left: 0, top: 0 });
    };
    const zoom = (delta) => {
      scale = Math.max(0.05, Math.min(1.8, Math.round((scale + delta) * 100) / 100));
      apply();
    };
    minus.addEventListener("click", () => zoom(-0.1));
    plus.addEventListener("click", () => zoom(0.1));
    fit.addEventListener("click", fitAll);
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let scrollX = 0;
    let scrollY = 0;
    viewport.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button, .edge-label")) return;
      dragging = true;
      startX = event.clientX;
      startY = event.clientY;
      scrollX = viewport.scrollLeft;
      scrollY = viewport.scrollTop;
      viewport.setPointerCapture(event.pointerId);
    });
    viewport.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      viewport.scrollLeft = scrollX - (event.clientX - startX);
      viewport.scrollTop = scrollY - (event.clientY - startY);
    });
    viewport.addEventListener("pointerup", () => { dragging = false; });
    viewport.addEventListener("pointercancel", () => { dragging = false; });
    viewport.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      zoom(event.deltaY < 0 ? 0.1 : -0.1);
    }, { passive: false });
    const resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(fitAll) : null;
    if (resizeObserver) resizeObserver.observe(viewport);
    host._graphCleanup = () => resizeObserver?.disconnect();
    apply();
    requestAnimationFrame(fitAll);
  }

  function setGraph(graphName) {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.graph === graphName));
    const title = graphName === "to_be_graph" ? "Agent 적용 후 권장 Flow" : "현재 업무 Flow";
    const copy = graphName === "to_be_graph"
      ? "실행 경로는 왼쪽에서 오른쪽으로, 분기·예외는 별도 직선 경로로 정리했습니다. 노드를 선택하면 구현·입출력 연결 설계를 확인할 수 있습니다."
      : "현재 사람이 수행하는 업무 단계와 분기·예외를 입력 설명 기반으로 정리했습니다. 노드를 선택하면 필요한 정보와 결과를 확인할 수 있습니다.";
    const titleTarget = document.getElementById("graph-title");
    const copyTarget = document.getElementById("graph-copy");
    if (titleTarget) titleTarget.textContent = title;
    if (copyTarget) copyTarget.textContent = copy;
    renderGraph(data[graphName] || {});
  }

  // The base report remains backward compatible, but this capture listener
  // replaces its curved legacy graph before a reader can interact with it.
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      event.stopPropagation();
      setGraph(tab.dataset.graph || "to_be_graph");
    }, true);
  });
  setGraph("to_be_graph");
})();
"""


# The original report script owns the graph drawing.  This second, small
# script is intentionally installed in the capture phase so it replaces the
# graph's generic object inspector without changing the graph renderer.  That
# keeps older view-models working while ensuring people never see raw JSON in
# the detail drawer.
DRAWER_INTERACTION_JS = r"""
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("report-data").textContent);
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("backdrop");
  const closeButton = document.getElementById("close");
  const drawerTitle = document.getElementById("drawer-title");
  const drawerKicker = document.getElementById("drawer-kicker");
  const drawerBody = document.getElementById("drawer-body");
  let returnFocus = null;

  const text = (value) => (typeof value === "string" ? value : (value == null ? "" : String(value)));
  const nonEmptyText = (value) => text(value).trim();
  const strings = (value) => Array.isArray(value)
    ? value.map(nonEmptyText).filter(Boolean)
    : (nonEmptyText(value) ? [nonEmptyText(value)] : []);
  const element = (tag, className, value) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  };

  // The full candidate set is still carried in the view model for traceable
  // planning, but people need the recommended and plausible alternatives —
  // not a long list of rejected search hits.
  document.querySelectorAll("#catalog-groups > section").forEach((section) => {
    if (nonEmptyText(section.querySelector("h3")?.textContent) === "사용하지 않은 검색 후보") section.remove();
  });

  function catalogLink(item) {
    if (!item || typeof item !== "object") return null;
    const assetId = nonEmptyText(item.asset_id).toLowerCase();
    const assetType = item.asset_type === "flow" ? "flow" : "component";
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(assetId)) return null;
    try {
      const parsed = new URL(nonEmptyText(item.catalog_url));
      const expectedHash = `#/${assetType}/${assetId}`;
      if (
        parsed.protocol !== "https:" ||
        parsed.hostname !== "agent-hub.skhynix.com" ||
        parsed.username || parsed.password || parsed.search ||
        parsed.pathname !== "/" || parsed.hash.toLowerCase() !== expectedHash
      ) return null;
      return parsed.href;
    } catch (_) {
      return null;
    }
  }

  function appendParagraph(host, heading, value) {
    const body = nonEmptyText(value);
    if (!body) return false;
    const section = element("section", "drawer-block");
    section.append(element("h3", "", heading), element("p", "", body));
    host.append(section);
    return true;
  }

  function appendList(host, heading, value) {
    const values = strings(value);
    if (!values.length) return false;
    const section = element("section", "drawer-block");
    const list = element("ul", "drawer-list");
    values.forEach((item) => list.append(element("li", "", item)));
    section.append(element("h3", "", heading), list);
    host.append(section);
    return true;
  }

  function appendCatalogCards(host, rawItems) {
    const items = Array.isArray(rawItems) ? rawItems.filter((item) => item && typeof item === "object") : [];
    if (!items.length) return false;
    const section = element("section", "drawer-block");
    const cards = element("div", "drawer-catalog-list");
    items.forEach((item) => {
      const card = element("article", "drawer-catalog-card");
      card.append(element("h4", "", nonEmptyText(item.title) || "카탈로그 자산"));
      const typeVersion = [
        item.asset_type === "flow" ? "Flow" : (item.asset_type ? "Component" : ""),
        nonEmptyText(item.version),
      ].filter(Boolean).join(" · ");
      if (typeVersion) card.append(element("p", "drawer-catalog-note", typeVersion));
      const reason = nonEmptyText(item.reason);
      if (reason) {
        const label = element("span", "drawer-catalog-label", "선정 이유");
        card.append(label, element("p", "", reason));
      }
      const checks = strings(item.required_verification || item.required_verifications);
      if (checks.length) {
        const label = element("span", "drawer-catalog-label", "연결 전 확인할 내용");
        const list = element("ul", "drawer-list");
        checks.forEach((check) => list.append(element("li", "", check)));
        card.append(label, list);
      }
      const href = catalogLink(item);
      if (href) {
        const link = element("a", "drawer-catalog-link", "Agent Hub 상세 보기 ↗");
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        card.append(link);
      }
      cards.append(card);
    });
    if (!cards.childElementCount) return false;
    section.append(element("h3", "", "참고할 카탈로그"), cards);
    host.append(section);
    return true;
  }

  function portItems(value) {
    if (Array.isArray(value)) return value.filter((item) => item && typeof item === "object");
    if (!value || typeof value !== "object") return [];
    return Object.entries(value).map(([portId, item]) => (
      item && typeof item === "object" ? { port_id: portId, ...item } : { port_id: portId, label: item }
    ));
  }

  function portLabel(port, fallback) {
    return nonEmptyText(port?.label)
      || nonEmptyText(port?.display_name)
      || nonEmptyText(port?.name)
      || nonEmptyText(port?.port_id)
      || fallback;
  }

  function portType(port) {
    const raw = port?.data_type ?? port?.type ?? port?.input_type ?? port?.output_type ?? port?.types;
    if (Array.isArray(raw)) return raw.map(nonEmptyText).filter(Boolean).join(" / ") || "Data";
    return nonEmptyText(raw) || "Data";
  }

  function ioStatus(plan) {
    const value = nonEmptyText(plan?.plan_status || plan?.status).toLowerCase();
    const requiresCheck = ["metadata", "check", "confirm", "verify", "review", "proposed", "draft"].some((token) => value.includes(token));
    if (value.includes("verified") || value.includes("ready")) return { label: "연결 설계 준비", requiresCheck: false };
    if (requiresCheck) return { label: "구현 전 포트 계약 확인", requiresCheck: true };
    return { label: "Langflow 연결 설계", requiresCheck: true };
  }

  function appendPortBadges(card, port) {
    const meta = element("div", "drawer-port-meta");
    meta.append(element("span", "drawer-port-type", portType(port)));
    meta.append(element("span", port?.required === true ? "drawer-port-required" : "drawer-port-optional", port?.required === true ? "필수" : "선택"));
    card.append(meta);
  }

  function currentStageTitle() {
    return nonEmptyText(drawerTitle?.textContent) || "현재 단계";
  }

  function appendRoutePrefix(host, label) {
    host.append(element("strong", "drawer-io-route-label", label));
  }

  function appendStageName(host, title) {
    host.append(element("strong", "drawer-stage-name", title));
  }

  function appendInputCard(host, port, external) {
    const card = element("article", "drawer-io-card");
    const label = portLabel(port, external ? "외부 입력" : "입력 포트");
    card.append(element("h4", "", `${external ? "외부 입력" : "입력"} · ${label}`));
    appendPortBadges(card, port);
    const sourceTitle = nonEmptyText(port?.source_node_title) || nonEmptyText(port?.source_node_id);
    const sourceOutput = nonEmptyText(port?.source_output_label)
      || nonEmptyText(port?.source_output_port_id)
      || "Output";
    const sourceType = nonEmptyText(port?.source_output_data_type) || "Data";
    const sourceKind = nonEmptyText(port?.source_kind).toLowerCase();
    const route = element("p", "drawer-io-route");
    const stageTitle = currentStageTitle();
    if (sourceTitle) {
      appendRoutePrefix(route, "앞 단계 연결 · ");
      appendStageName(route, sourceTitle);
      route.append(document.createTextNode(`의 Output ${sourceOutput} (${sourceType}) → `));
      appendStageName(route, stageTitle);
      route.append(document.createTextNode(`의 Input ${label} (${portType(port)})`));
    } else if (external || sourceKind === "external_input" || sourceKind === "chat_input" || sourceKind === "text_input") {
      appendRoutePrefix(route, "외부 입력 · ");
      route.append(document.createTextNode("사용자 또는 시작 Input → "));
      appendStageName(route, stageTitle);
      route.append(document.createTextNode(`의 Input ${label} (${portType(port)})`));
    } else {
      appendRoutePrefix(route, "연결 필요 · ");
      route.append(document.createTextNode("앞 단계의 호환 Output → "));
      appendStageName(route, stageTitle);
      route.append(document.createTextNode(`의 Input ${label} (${portType(port)})`));
    }
    card.append(route);
    const connectionLabel = nonEmptyText(port?.connection_label) || nonEmptyText(port?.description);
    if (connectionLabel) card.append(element("p", "drawer-io-route", `연결 목적 · ${connectionLabel}`));
    host.append(card);
  }

  function appendOutputCard(host, port) {
    const card = element("article", "drawer-io-card");
    const label = portLabel(port, "출력 포트");
    const stageTitle = currentStageTitle();
    card.append(element("h4", "", `출력 · ${label}`));
    appendPortBadges(card, port);
    const bindings = Array.isArray(port?.downstream_bindings)
      ? port.downstream_bindings.filter((item) => item && typeof item === "object")
      : [];
    if (!bindings.length) {
      const empty = element("p", "drawer-io-empty");
      appendStageName(empty, stageTitle);
      empty.append(document.createTextNode(`의 Output ${label} (${portType(port)})은 최종 보고서, Chat Output 또는 다음 Flow의 Input으로 연결합니다.`));
      card.append(empty);
    } else {
      const bindingHost = element("div", "drawer-io-bindings");
      bindings.forEach((binding) => {
        const targetTitle = nonEmptyText(binding.target_node_title) || nonEmptyText(binding.target_node_id) || "다음 단계";
        const targetInput = nonEmptyText(binding.target_input_label) || nonEmptyText(binding.target_input_port_id) || "Input";
        const targetType = nonEmptyText(binding.target_input_data_type) || "Data";
        const edgeLabel = nonEmptyText(binding.edge_label);
        const line = element("div", "drawer-io-binding");
        appendRoutePrefix(line, "다음 단계 · ");
        appendStageName(line, stageTitle);
        line.append(document.createTextNode(`의 Output ${label} (${portType(port)}) → `));
        appendStageName(line, targetTitle);
        line.append(document.createTextNode(`의 Input ${targetInput} (${targetType})`));
        if (edgeLabel) line.append(document.createTextNode(` · 경로: ${edgeLabel}`));
        bindingHost.append(line);
      });
      card.append(bindingHost);
    }
    host.append(card);
  }

  function appendLangflowIoPlan(host, rawPlan) {
    const plan = rawPlan && typeof rawPlan === "object" ? rawPlan : null;
    if (!plan) return false;
    const inputs = portItems(plan.inputs || plan.input_ports);
    const outputs = portItems(plan.outputs || plan.output_ports);
    const externalInputs = portItems(plan.external_inputs || plan.externalInputs);
    const note = nonEmptyText(plan.plan_note) || nonEmptyText(plan.implementation_note);
    if (!inputs.length && !outputs.length && !externalInputs.length && !note) return false;

    const section = element("section", "drawer-block drawer-io-section");
    section.append(element("h3", "", "Langflow 1.11 연결 설계"));
    section.append(element("p", "drawer-io-intro", "이 단계가 받는 Input과 다음 단계로 내보낼 Output을 Langflow Canvas 연결 기준으로 정리했습니다."));
    const status = ioStatus(plan);
    section.append(element("span", `drawer-io-status${status.requiresCheck ? " needs-check" : ""}`, status.label));
    const grid = element("div", "drawer-io-grid");
    inputs.forEach((port) => appendInputCard(grid, port, false));
    externalInputs.forEach((port) => appendInputCard(grid, port, true));
    outputs.forEach((port) => appendOutputCard(grid, port));
    section.append(grid);
    const notes = element("div", "drawer-io-notes");
    if (note) notes.append(element("p", `drawer-io-note${status.requiresCheck ? " needs-check" : ""}`, note));
    if (status.requiresCheck) {
      notes.append(element("p", "drawer-io-note needs-check", "카탈로그 자산이 metadata_only이거나 포트 계약이 확정되지 않은 경우에는 실제 Component/Flow의 Input·Output 이름과 타입을 확인한 뒤 Canvas에 연결하세요."));
    }
    if (notes.childElementCount) section.append(notes);
    host.append(section);
    return true;
  }

  function graphNodeForButton(button) {
    const selectedTab = document.querySelector(".tab.active");
    const graphName = selectedTab?.dataset?.graph || "to_be_graph";
    const graph = data[graphName] || {};
    const nodeTitle = nonEmptyText(button.querySelector("h3")?.textContent);
    const stepText = nonEmptyText(button.querySelector(".node-meta span:last-child")?.textContent);
    const stepMatch = stepText.match(/(\d+)/);
    const oneBasedSequence = stepMatch ? Number(stepMatch[1]) : null;
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    return nodes.find((item) => (
      nonEmptyText(item?.title) === nodeTitle &&
      (oneBasedSequence == null || Number(item?.sequence) + 1 === oneBasedSequence)
    )) || nodes.find((item) => nonEmptyText(item?.title) === nodeTitle) || null;
  }

  function openHumanDrawer(title, fields, trigger) {
    returnFocus = trigger instanceof HTMLElement ? trigger : document.activeElement;
    drawerTitle.textContent = nonEmptyText(title) || "업무 단계 상세";
    if (drawerKicker) drawerKicker.textContent = "업무 이해와 구현 판단에 필요한 정보만 표시합니다.";
    drawerBody.replaceChildren();

    const detail = fields && typeof fields === "object" ? fields : {};
    let count = 0;
    count += appendParagraph(drawerBody, "이 단계에서 하는 일", detail.current_work || detail.description) ? 1 : 0;
    count += appendList(drawerBody, "현재 확인된 주의점", detail.problems) ? 1 : 0;
    count += appendParagraph(drawerBody, "개선 방향", detail.improvement) ? 1 : 0;
    count += appendLangflowIoPlan(drawerBody, detail.implementation_io_plan || detail.langflow_io_plan) ? 1 : 0;
    count += appendList(drawerBody, "필요한 정보", detail.inputs) ? 1 : 0;
    count += appendList(drawerBody, "만드는 결과", detail.outputs) ? 1 : 0;
    count += appendCatalogCards(drawerBody, detail.catalog_recommendations || detail.catalog_application) ? 1 : 0;
    if (!count) drawerBody.append(element("p", "drawer-empty", "이 단계에 추가로 표시할 업무 정보가 없습니다."));

    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.classList.add("open");
    requestAnimationFrame(() => closeButton.focus());
  }

  function closeHumanDrawer() {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    backdrop.classList.remove("open");
    if (returnFocus && document.contains(returnFocus)) requestAnimationFrame(() => returnFocus.focus());
  }

  function focusableInDrawer() {
    return Array.from(drawer.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'))
      .filter((item) => item instanceof HTMLElement && !item.hidden);
  }

  // Capture phase prevents the legacy generic inspector from rendering its
  // raw object values, while Enter/Space continue to produce a normal click.
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button.flow-node") : null;
    if (!button) return;
    event.stopImmediatePropagation();
    event.stopPropagation();
    const selectedTab = document.querySelector(".tab.active");
    const graph = data[selectedTab?.dataset?.graph || "to_be_graph"] || {};
    const node = graphNodeForButton(button);
    const details = graph.details && node ? graph.details[node.detail_ref] : null;
    openHumanDrawer(node?.title || button.querySelector("h3")?.textContent, details || { description: node?.summary || "" }, button);
  }, true);

  closeButton.addEventListener("click", (event) => {
    event.stopImmediatePropagation();
    closeHumanDrawer();
  }, true);
  backdrop.addEventListener("click", (event) => {
    event.stopImmediatePropagation();
    closeHumanDrawer();
  }, true);
  document.addEventListener("keydown", (event) => {
    if (!drawer.classList.contains("open")) return;
    if (event.key === "Escape") {
      event.stopImmediatePropagation();
      closeHumanDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    const targets = focusableInDrawer();
    if (!targets.length) return;
    const first = targets[0];
    const last = targets[targets.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, true);
})();
"""


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


class ResponsiveReportRendererV2Component(Component):
    """07. Turn report-view-model/v2 into a deterministic self-contained HTML report."""

    display_name = "07 업무 설계 HTML 보고서"
    description = "검증된 화면 계약만 사용해 안전한 반응형 업무 설계 HTML 보고서를 생성합니다."
    icon = "PanelsTopLeft"
    name = "ResponsiveReportRendererV2"

    inputs = [
        DataInput(name="report_view_model", display_name="Report View Model", required=True),
        IntInput(name="max_nodes", display_name="그래프당 최대 Node 수", value=500, advanced=True),
        IntInput(name="max_edges", display_name="그래프당 최대 Edge 수", value=1000, advanced=True),
        IntInput(name="max_html_bytes", display_name="최대 HTML Bytes", value=10_000_000, advanced=True),
    ]
    outputs = [Output(name="render_result", display_name="Rendered Report", method="render_report", types=["Data"])]

    def render_report(self) -> Data:
        view_model = _payload(self.report_view_model)
        if view_model.get("schema_version") != _SCHEMA or view_model.get("renderer_version") != _RENDERER:
            raise ValueError("[REPORT_RENDER_FAILED] report-view-model/v2와 business-report-renderer.v2 계약이 필요합니다. 06 node 출력을 확인해 주세요.")
        if _contains_secret(view_model):
            raise ValueError("[REPORT_SECRET_DETECTED] Report View Model에 민감정보로 의심되는 값이 있습니다. 마스킹된 업무 설명으로 다시 실행해 주세요.")
        report_id = str(view_model.get("report_id") or "")
        if _REPORT_ID.fullmatch(report_id) is None or report_id != _expected_report_id(view_model):
            raise ValueError("[REPORT_RENDER_FAILED] report_id가 canonical View Model과 일치하지 않습니다. 06 node 출력을 다시 생성해 주세요.")
        max_nodes = _bounded_int(getattr(self, "max_nodes", 500), 500, 1, 2000)
        max_edges = _bounded_int(getattr(self, "max_edges", 1000), 1000, 1, 5000)
        for graph_name in ("as_is_graph", "to_be_graph"):
            graph = view_model.get(graph_name)
            if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
                raise ValueError(f"[REPORT_RENDER_FAILED] {graph_name} 구조가 올바르지 않습니다. 06 node 출력을 확인해 주세요.")
            if len(graph["nodes"]) > max_nodes or len(graph["edges"]) > max_edges:
                raise ValueError("[REPORT_RENDER_FAILED] 그래프 크기가 Renderer 한도를 초과했습니다. 최대 node/edge 설정을 확인해 주세요.")
        # report_id validates the original, canonical data contract above.  The
        # separately embedded display projection is scrubbed again so a bad
        # URL cannot survive as inert-but-still-visible JSON in the HTML.
        display_model = _scrub_catalog_urls(view_model)
        payload = _escaped_json(display_model)
        document = (
            "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"renderer-version\" content=\"business-report-renderer.v2\"><title>업무 방식 및 개선 실행 보고서</title><style>"
            + CSS
            + DRAWER_CSS
            + GRAPH_LAYOUT_CSS
            + DRAWER_IO_CSS
            + REFINEMENT_CSS
            + IMPLEMENTATION_SUMMARY_CSS
            + "</style></head><body><main class=\"shell\"><header class=\"top\"><div class=\"brand\"><div class=\"mark\">A</div><span>업무 설계 보고서</span></div><div class=\"meta\">단일 Flow · 카탈로그 기반 설계안</div></header>"
            + "<section class=\"hero\"><div class=\"hero-main\"><div class=\"eyebrow\">BUSINESS WORK DESIGN</div><h1 id=\"report-title\">업무 방식 및 개선 실행 보고서</h1><p class=\"lead\">입력한 업무 설명과 기능 카탈로그 후보를 기반으로, 현재 업무와 개선 실행안을 한 화면에서 확인합니다.</p></div><div class=\"hero-status\"><div class=\"status-card\"><span id=\"status-dot\" class=\"status-dot\"></span><span id=\"status-label\">설계 결과</span></div><div id=\"quick\" class=\"quick\"></div></div></section>"
            + "<div class=\"grid\"><section class=\"card section wide\"><div class=\"section-title\"><div><h2>Agent 구현 한눈에 보기</h2><p>하단의 업무 분석·Flow·카탈로그 상세와 겹치지 않도록, 실제 구현 방향만 짧게 보여 줍니다.</p></div></div><div id=\"summary-grid\" class=\"summary-grid\"></div></section>"
            + "<section id=\"refinement-section\" class=\"card section wide\" hidden><div class=\"section-title\"><div><h2>설계 보완 반영</h2><p>초안 이후의 보완 요청과 반영 상태를 간단히 확인합니다.</p></div><div id=\"refinement-status\" class=\"badges\"></div></div><div id=\"refinement-content\" class=\"refinement-content\"></div></section>"
            + "<section class=\"card section wide\"><div class=\"section-title\"><div><h2>입력한 업무 설명 원문</h2><p>다음 실행에서 수정할 수 있는 안전한 표시용 원문입니다.</p></div></div><div id=\"source-description\" class=\"source\"></div><p id=\"source-note\" class=\"note\"></p></section>"
            + "<section class=\"card section wide\"><div class=\"section-title\"><div><h2>추가 보완이 필요한 내용</h2><p>질문을 입력받아 멈추지 않습니다. 아래 문장을 업무 설명에 추가한 뒤 전체 Flow를 다시 실행하세요.</p></div></div><div id=\"gaps\" class=\"gap-list\"></div></section>"
            + "<section class=\"card section half\"><div class=\"section-title\"><div><h2>업무 범위와 운영 맥락</h2><p>시스템이 이해한 업무 범위와 현재 제약입니다.</p></div></div><div id=\"context\" class=\"detail-grid\"></div></section>"
            + "<section class=\"card section half\"><div class=\"section-title\"><div><h2>설계 적용 원칙</h2><p>카탈로그는 후보이며, 실제 연결 전 기술 계약과 권한을 확인합니다.</p></div></div><div class=\"badges\"><span class=\"badge orange\">카탈로그 후보는 적용 확정이 아닙니다</span><span class=\"badge violet\">사람 검토는 실행 HITL이 아닌 업무 단계입니다</span></div><p class=\"note\">보고서의 그래프는 구현 제안이며, 이 Flow가 자동으로 외부 시스템을 실행하지는 않습니다.</p></section>"
            + "<section class=\"card section wide\"><div class=\"tabs\"><button type=\"button\" class=\"tab\" data-graph=\"as_is_graph\">현재 업무 Flow</button><button type=\"button\" class=\"tab active\" data-graph=\"to_be_graph\">Agent 적용 후 권장 Flow</button></div><div class=\"section-title\"><div><h2 id=\"graph-title\">Agent 적용 후 권장 Flow</h2><p id=\"graph-copy\"></p></div></div><div id=\"graph-host\"></div></section>"
            + "<section class=\"card section wide\"><div class=\"section-title\"><div><h2>카탈로그 기반 적용 계획</h2><p>어느 업무 단계에 어떤 기존 Component·Flow를 적용할지, 선택 이유와 Agent Hub 상세 링크를 함께 확인합니다.</p></div></div><div id=\"catalog-groups\" class=\"catalog-groups\"></div></section>"
            + "<section class=\"card section half\"><div class=\"section-title\"><div><h2>구현 로드맵</h2><p>작은 검증 단위로 구현하고, 완료 기준을 충족한 뒤 다음 단계로 진행합니다.</p></div></div><div id=\"roadmap\" class=\"roadmap\"></div></section>"
            + "<section class=\"card section half\"><div class=\"section-title\"><div><h2>위험·통제와 검증 시나리오</h2><p>실제 연결 전 확인할 위험과 테스트 기준입니다.</p></div></div><h3>위험과 통제</h3><div id=\"risks\" class=\"detail-grid\"></div><h3 style=\"margin-top:15px\">검증 시나리오</h3><div id=\"tests\" class=\"detail-grid\"></div></section>"
            # The trace remains in the in-memory view model for deterministic
            # render validation, but is intentionally not a reader-facing
            # report section.  The hidden element keeps the legacy graph
            # script compatible until that script is retired.
            + "<pre id=\"trace\" hidden aria-hidden=\"true\"></pre></div>"
            + "</main><div id=\"backdrop\" class=\"drawer-backdrop\"></div><aside id=\"drawer\" class=\"drawer\" role=\"dialog\" aria-modal=\"true\" aria-hidden=\"true\" aria-labelledby=\"drawer-title\"><div class=\"drawer-head\"><div class=\"drawer-head-copy\"><h2 id=\"drawer-title\">업무 단계 상세</h2><p id=\"drawer-kicker\" class=\"drawer-kicker\"></p></div><button id=\"close\" class=\"close\" type=\"button\" aria-label=\"상세 닫기\">×</button></div><div id=\"drawer-body\" class=\"drawer-body\"></div></aside>"
            + "<script id=\"report-data\" type=\"application/json\">"
            + payload
            + "</script><script>"
            + JS
            + GRAPH_LAYOUT_JS
            + DRAWER_INTERACTION_JS
            + "</script></body></html>"
        )
        max_bytes = _bounded_int(getattr(self, "max_html_bytes", 10_000_000), 10_000_000, 100_000, 15_000_000)
        byte_count = len(document.encode("utf-8"))
        if byte_count > max_bytes:
            raise ValueError("[REPORT_RENDER_FAILED] 생성된 HTML이 최대 크기를 초과했습니다. 보고서 데이터 또는 최대 HTML Bytes 설정을 확인해 주세요.")
        completion = view_model.get("completion_status") if isinstance(view_model.get("completion_status"), dict) else {}
        result = {
            "ok": True,
            "status": "RENDERED",
            "schema_version": "render-result/v2",
            "report_id": report_id,
            "renderer_version": _RENDERER,
            "title": view_model.get("title") or "업무 방식 및 개선 실행 보고서",
            "html": document,
            "content_sha256": "sha256:" + _sha256(document),
            "script_csp_hash": _csp_hash(JS + GRAPH_LAYOUT_JS + DRAWER_INTERACTION_JS),
            "style_csp_hash": _csp_hash(CSS + DRAWER_CSS + GRAPH_LAYOUT_CSS + DRAWER_IO_CSS + REFINEMENT_CSS + IMPLEMENTATION_SUMMARY_CSS),
            "byte_count": byte_count,
            "report_summary": {
                "completion_status": completion.get("code") or "COMPLETED",
                "information_gap_count": completion.get("information_gap_count") if isinstance(completion.get("information_gap_count"), int) else 0,
                "catalog_candidate_count": completion.get("catalog_candidate_count") if isinstance(completion.get("catalog_candidate_count"), int) else 0,
                "catalog_selected_count": completion.get("catalog_selected_count") if isinstance(completion.get("catalog_selected_count"), int) else 0,
                "catalog_considered_count": len((view_model.get("catalog_application_plan") or {}).get("considered") or []),
            },
        }
        self.status = f"HTML 보고서 생성 완료 · {byte_count:,} bytes"
        return Data(data=result)
