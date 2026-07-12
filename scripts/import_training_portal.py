from __future__ import annotations

import hashlib
import html
import json
import shutil
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_TRAINING = ROOT / "html" / "training"

TOC_ITEMS = [
    ("overview", "개요"),
    ("component-basics", "Custom Node 기본"),
    ("input-output", "Input / Output"),
    ("basic-nodes", "기본 노드"),
    ("official-visuals", "공식 화면 예시"),
    ("community-patterns", "커뮤니티 패턴"),
    ("recipes", "공통 Component"),
    ("flow-patterns", "Flow 구현 패턴"),
    ("branch-routing", "조건 분기"),
    ("mcp-integration", "MCP 연동"),
    ("validation", "검증 체크리스트"),
    ("sources", "공식 참고 문서"),
]


class ContentAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.heading_stack: list[list[str]] = []
        self.headings: list[tuple[str, str]] = []
        self.references: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))
        if tag in {"h1", "h2", "h3"}:
            self.heading_stack.append([tag, ""])
        if tag == "a" and attributes.get("href"):
            self.references.append(str(attributes["href"]))
        if tag in {"img", "script"} and attributes.get("src"):
            self.references.append(str(attributes["src"]))

    def handle_data(self, data: str) -> None:
        if self.heading_stack:
            self.heading_stack[-1][1] += data

    def handle_endtag(self, tag: str) -> None:
        if self.heading_stack and self.heading_stack[-1][0] == tag:
            heading, value = self.heading_stack.pop()
            self.headings.append((heading, " ".join(value.split())))


def find_legacy_root() -> Path:
    desktop = Path.home() / "Desktop"
    matches = [path.parent for path in desktop.glob("*/LANGFLOW_INTERNAL_TRAINING_PORTAL.html")]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one legacy training portal, found {matches}")
    return matches[0]


def extract_style(source: str) -> str:
    start_marker = "<style>"
    end_marker = "</style>"
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    return source[start:end].strip() + "\n"


def extract_content(source: str) -> str:
    start_marker = '<div class="content">'
    end_marker = "\n      </div>\n    </main>"
    start = source.index(start_marker) + len(start_marker)
    end = source.rfind(end_marker)
    if end <= start:
        raise RuntimeError("Could not isolate the legacy training content area")
    return source[start:end]


def navigation(current: str) -> str:
    items = [
        ("home", "../index.html", "홈"),
        ("training", "index.html", "처음 배우기"),
        ("flows", "../flows/index.html", "Flows"),
        ("components", "../components/index.html", "Components"),
        ("troubleshooting", "../troubleshooting/index.html", "실패와 해결"),
        ("business", "../business-agent-design/index.html", "업무 Agent 설계"),
    ]
    return "".join(
        f'<a href="{href}"' + (' aria-current="page"' if key == current else "") + f'>{label}</a>'
        for key, href, label in items
    )


def build_page(content: str) -> str:
    toc = "".join(f'<li><a href="#{section_id}">{html.escape(label)}</a></li>' for section_id, label in TOC_ITEMS)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Langflow 전체 교육 포털 · Agent Ground</title>
  <meta name="description" content="기존 교육 내용을 모두 유지하고 Agent Ground 디자인으로 재구성한 Langflow 교육 포털" />
  <link rel="stylesheet" href="../assets/css/training-content-base.css" />
  <link rel="stylesheet" href="../assets/css/site.css" />
  <link rel="stylesheet" href="../assets/css/training-full.css" />
</head>
<body>
  <a class="skip-link" href="#training-content">본문으로 바로가기</a>
  <header class="site-header">
    <div class="header-inner">
      <a class="brand" href="../index.html" aria-label="Agent Ground 홈">
        <span class="brand-mark" aria-hidden="true">AG</span>
        <span class="brand-copy"><strong>Agent Ground</strong><small>Learn · Build · Validate</small></span>
      </a>
      <button class="menu-button" type="button" data-menu-button aria-expanded="false" aria-controls="site-nav" aria-label="메뉴 열기"><span class="menu-lines"></span></button>
      <nav class="site-nav" id="site-nav" data-site-nav data-open="false" aria-label="주요 메뉴">{navigation('training')}</nav>
    </div>
  </header>

  <main class="training-page" id="training-content">
    <nav class="breadcrumbs" aria-label="현재 위치"><a href="../index.html">홈</a><span aria-hidden="true">/</span><span>전체 교육 포털</span></nav>
    <section class="training-title-hero" aria-labelledby="training-title">
      <div>
        <p class="training-kicker">Complete Langflow Curriculum</p>
        <h1 id="training-title">Langflow Study 자료</h1>
        <p>기존에 축적한 Custom Node, 타입 연결, 기본 노드, RAG·Milvus·Loop·분기·MCP, 실습 예제와 검증 내용을 모두 유지하면서 Agent Ground 디자인과 탐색 구조로 다시 배치했습니다.</p>
      </div>
      <div class="training-title-actions">
        <span class="badge badge-testing">실제 환경 검증 중</span>
        <a class="button button-primary" href="overview.html">초보자 학습 안내</a>
      </div>
    </section>

    <div class="training-layout">
      <aside class="training-toc-panel" aria-label="교육 목차">
        <div class="training-toc-head"><span>Curriculum</span><strong>전체 목차</strong></div>
        <ol class="training-toc">{toc}</ol>
        <div class="training-toc-note"><strong>내용 보존 원칙</strong><p>기존 교육 본문과 예제는 유지하고 화면 구조와 디자인만 재구성했습니다.</p><a href="https://docs.langflow.org/concepts-file-management" target="_blank" rel="noopener noreferrer">Langflow 파일 관리 참고</a></div>
      </aside>

      <div class="training-main-column">
        <div class="training-toolbar">
          <label class="training-search" for="searchInput"><span aria-hidden="true">⌕</span><input id="searchInput" type="search" placeholder="예: FileInput, RAG, Loop, MCP, validation" aria-label="교육자료 검색" /></label>
          <div class="training-toolbar-actions">
            <button type="button" id="expandCode">코드 위치 찾기</button>
            <button type="button" id="printPage">PDF 저장</button>
          </div>
        </div>
        <article class="training-article">
{content}
        </article>
      </div>
    </div>

    <div class="page-actions training-bottom-actions"><button class="back-button" type="button" data-back data-fallback="../index.html">← 이전 화면</button><a class="button button-soft" href="../index.html">홈</a><a class="button button-soft" href="../flows/index.html">Flow 목록</a><a class="button button-soft" href="../troubleshooting/index.html">실패와 해결</a></div>
  </main>

  <footer class="site-footer"><div class="footer-inner"><div><strong>Agent Ground</strong><p>축적된 교육 내용을 잃지 않으면서 실제 Agent Builder 환경에 맞게 계속 검증하고 개선합니다.</p></div><div class="footer-links"><a href="overview.html">학습 안내</a><a href="../flows/index.html">Flows</a><a href="../../AGENT_GROUND_PROJECT_MASTER_GUIDE.md">프로젝트 기준</a></div></div></footer>
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
  <script src="../assets/js/site.js"></script>
  <script src="../assets/js/training-full.js"></script>
</body>
</html>
"""


def main() -> None:
    legacy_root = find_legacy_root()
    source_path = legacy_root / "LANGFLOW_INTERNAL_TRAINING_PORTAL.html"
    source = source_path.read_text(encoding="utf-8-sig")
    content = extract_content(source)
    legacy_style = extract_style(source)
    imported = build_page(content)

    HTML_TRAINING.mkdir(parents=True, exist_ok=True)
    target_path = HTML_TRAINING / "index.html"
    target_path.write_text(imported, encoding="utf-8")
    (ROOT / "html" / "assets" / "css" / "training-content-base.css").write_text(legacy_style, encoding="utf-8")

    for folder_name in ("assets", "examples", "sample_files", "scripts"):
        source_folder = legacy_root / folder_name
        if source_folder.is_dir():
            shutil.copytree(source_folder, HTML_TRAINING / folder_name, dirs_exist_ok=True)

    references = HTML_TRAINING / "references"
    references.mkdir(parents=True, exist_ok=True)
    guide = legacy_root / "LANGFLOW_CUSTOM_NODE_CODE_GUIDE.md"
    if guide.is_file():
        shutil.copy2(guide, references / guide.name)

    source_audit = ContentAudit()
    source_audit.feed(source)
    target_audit = ContentAudit()
    target_audit.feed(imported)
    if source_audit.headings != target_audit.headings:
        raise RuntimeError("Training heading sequence changed during redesign")
    if not set(source_audit.references).issubset(set(target_audit.references)):
        missing = sorted(set(source_audit.references) - set(target_audit.references))
        raise RuntimeError(f"Legacy references were lost during redesign: {missing}")
    if content not in imported:
        raise RuntimeError("Legacy training content markup is not intact in the redesigned page")

    manifest = {
        "source_path": str(source_path),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "content_markup_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "source_lines": len(source.splitlines()),
        "source_headings": len(source_audit.headings),
        "target_headings": len(target_audit.headings),
        "source_references": len(source_audit.references),
        "references_preserved": set(source_audit.references).issubset(set(target_audit.references)),
        "content_markup_preserved": content in imported,
        "target_path": str(target_path),
        "design_mode": "agent_ground_restructured",
        "policy": "Keep the complete legacy content area while replacing the legacy shell, navigation, and visual design.",
    }
    manifest_path = ROOT / "training" / "source" / "legacy_training_portal_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "TRAINING_PORTAL_REDESIGNED",
        json.dumps(
            {
                "source_lines": manifest["source_lines"],
                "headings": manifest["source_headings"],
                "references": manifest["source_references"],
                "content_markup_preserved": manifest["content_markup_preserved"],
                "design_mode": manifest["design_mode"],
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
