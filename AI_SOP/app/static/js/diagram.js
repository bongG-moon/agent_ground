let mermaidModulePromise;
let diagramSequence = 0;

function extractDefinition(markdown) {
  const match = markdown.match(/```mermaid\s*\n([\s\S]*?)```/i);
  if (!match) throw new Error("Mermaid 흐름도 블록을 찾을 수 없습니다.");
  return match[1].trim();
}

async function loadMermaid() {
  if (!mermaidModulePromise) {
    mermaidModulePromise = import("../vendor/mermaid/mermaid.esm.min.mjs").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        fontFamily: "Pretendard, Noto Sans KR, Apple SD Gothic Neo, sans-serif",
        themeVariables: {
          primaryColor: "#e8edfa",
          primaryTextColor: "#303a50",
          primaryBorderColor: "#6d88dd",
          lineColor: "#7d8ca9",
          secondaryColor: "#f2f5fb",
          tertiaryColor: "#ffffff",
          edgeLabelBackground: "#edf1f8",
          fontSize: "15px",
        },
        flowchart: {
          curve: "linear",
          htmlLabels: false,
          useMaxWidth: true,
          nodeSpacing: 46,
          rankSpacing: 72,
          diagramPadding: 20,
          wrappingWidth: 190,
        },
      });
      return mermaid;
    });
  }
  return mermaidModulePromise;
}

export async function renderMermaidDiagram(container, markdown) {
  const mermaid = await loadMermaid();
  const definition = extractDefinition(markdown);
  const diagramId = `ai-sop-flow-${++diagramSequence}`;
  const { svg, bindFunctions } = await mermaid.render(diagramId, definition);
  container.replaceChildren();
  container.insertAdjacentHTML("afterbegin", svg);
  bindFunctions?.(container);
}
