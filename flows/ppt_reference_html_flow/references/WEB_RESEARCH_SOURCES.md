# 웹 조사 근거와 Flow 반영 내용

조사 기준일: 2026-07-15

기술 구현과 프롬프트 원칙은 공식 문서와 원 제공자의 공개 자료만 참고했습니다. 외부 자료의 문장을 프로젝트 프롬프트에 그대로 복사하지 않고 핵심 원칙을 한국어 실행 계약으로 재해석했습니다.

## 0. 디자인 정책과 모션 검사

### Hallmark

- [Nutlope Hallmark 공식 저장소](https://github.com/Nutlope/hallmark)

Flow 반영:

- 디자인 지침을 Prompt 한 곳에 넣지 않고 `design_policy` 계약으로 분리
- 슬라이드 역할, 시각 무게, 한 슬라이드 한 메시지, layout 반복과 콘텐츠 밀도 검사
- 모든 내용을 균일한 카드로 만드는 패턴과 장식용 gradient를 프로젝트 정책으로 제한
- 원문 Skill을 복제하지 않고 이 Flow의 프레젠테이션 계약에 맞는 규칙만 새로 작성

### Emil Kowalski Skills

- [emilkowalski/skills 공식 저장소](https://github.com/emilkowalski/skills)

Flow 반영:

- `transition: all`, `scale(0)`, UI의 `ease-in`, layout 속성 animation 금지
- UI 모션 300ms 이하, transform·opacity 중심, reduced-motion 대응
- hover를 fine pointer 환경으로 제한하고 키보드 탐색에는 슬라이드 모션을 적용하지 않음
- Renderer가 허용된 모션만 결정론적으로 생성하고 Quality Gate가 정적으로 다시 검사

두 저장소는 MIT License로 공개되어 있습니다. 이 프로젝트는 문구나 전체 Skill을 복제하지 않고 프로젝트 소유의 구조화 정책과 테스트로 재작성했습니다.

## 1. Agent Skill과 프레젠테이션 작성

### Anthropic Agent Skills

- [Agent Skills 개요](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Anthropic 공식 Skills 저장소](https://github.com/anthropics/skills)
- [PPTX Skill 원문](https://github.com/anthropics/skills/blob/main/skills/pptx/SKILL.md?plain=1)
- [Prompting Best Practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
- [Prompt Template과 변수](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-tools)

반영한 원칙:

- Skill은 반복 가능한 지침·리소스·스크립트 묶음으로 관리
- 고정 지침과 동적 입력을 분리
- 성공 기준, 순차 처리 단계와 출력 형식을 명시
- 표지·본문의 시각 계층, 여백과 반복 모티프를 일관되게 사용
- 최초 생성 결과를 완성품으로 간주하지 않고 오버플로·충돌·대비를 검수

라이선스 주의:

Anthropic 저장소의 PPTX 문서 Skill은 저장소 설명과 해당 폴더의 라이선스에 따라 source-available proprietary 자료입니다. 이 프로젝트에는 문장을 복제하지 않고 일반적인 작성·QA 원칙만 새로 서술했습니다.

### OpenAI Skills

- [Codex App과 Skills](https://openai.com/index/introducing-the-codex-app/)
- [OpenAI Skill Creator](https://github.com/openai/skills/blob/main/skills/.system/skill-creator/SKILL.md)

반영한 원칙:

- 지침과 실행 자산을 분리
- 필요한 작업에만 전문 지침을 적용
- 출력 템플릿과 검증 절차를 재사용 가능한 형태로 관리

## 2. 이미지 입력과 멀티모달 분석

- [OpenAI API 이미지 입력 계약](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal/delta?lang=curl)
- [OpenAI Image Inputs FAQ](https://help.openai.com/en/articles/8400551-image-inputs-for-chatgpt-faq)

확인한 내용:

- 이미지 입력은 완전한 URL 또는 Base64 data URL을 사용할 수 있음
- 이미지 detail은 지원 모델에서 `high`, `low`, `auto` 등으로 지정 가능
- 비라틴 문자, 회전된 문자, 정확한 공간 위치, 개수와 색·선 스타일로 구분된 그래프는 오해할 수 있음
- 이미지는 처리 중 크기가 변경될 수 있어 정확한 원본 좌표 복원에 적합하지 않음

Flow 반영:

- Encoder 출력은 `data_url`을 사용
- 표지와 본문 역할을 별도 입력으로 전달
- 참고 이미지 OCR을 사실 데이터로 사용하지 않음
- 관찰과 추정, 신뢰도와 경고를 분리
- 실제 Vision 모델 E2E 전에는 분석 정확도를 확정하지 않음

## 3. HTML 프레젠테이션

- [Reveal.js](https://revealjs.com/)
- [Presentation Size](https://revealjs.com/presentation-size/)
- [Layout](https://revealjs.com/layout/)
- [Configuration](https://revealjs.com/config/)
- [Keyboard](https://revealjs.com/keyboard/)
- [Speaker View](https://revealjs.com/speaker-view/)
- [PDF Export](https://revealjs.com/pdf-export/)

반영한 원칙:

- 기준 크기와 종횡비를 유지하면서 화면에 맞게 확대·축소
- 키보드, 터치, 슬라이드 번호와 진행 표시 지원
- 슬라이드별 발표자 노트 지원
- PDF 인쇄 시 한 슬라이드가 한 페이지를 넘지 않도록 제한
- 표지·본문·섹션·차트 중심 등의 layout을 명시적으로 분리

Renderer가 Reveal.js를 직접 포함할지, 동일 계약을 자체 JavaScript로 구현할지는 배포 환경의 오프라인 요구와 라이브러리 제공 방식을 확인한 뒤 결정합니다. 프롬프트는 특정 라이브러리 코드를 생성하지 않습니다.

## 4. 데이터 시각화

- [Vega-Lite 개요](https://vega.github.io/vega-lite/docs/)
- [Vega-Lite Specification](https://vega.github.io/vega-lite/docs/spec.html)
- [Encoding](https://vega.github.io/vega-lite/docs/encoding.html)
- [Scale](https://vega.github.io/vega-lite/docs/scale.html)
- [Bar](https://vega.github.io/vega-lite/docs/bar.html)
- [Line](https://vega.github.io/vega-lite/docs/line.html)
- [ARIA Configuration](https://vega.github.io/vega-lite/docs/config.html)
- [Chart.js Accessibility](https://www.chartjs.org/docs/latest/general/accessibility.html)

반영한 원칙:

- 필드를 quantitative, temporal, ordinal, nominal 의미 타입으로 분리
- 데이터 필드를 x, y, color, shape, text와 description 같은 채널에 명시적으로 연결
- 시간 변화에는 line, 범주 비교와 histogram에는 bar 계열 mark 사용
- 막대와 영역 차트의 정량 축은 기본적으로 0을 포함
- SVG 출력에서는 ARIA 설명을 사용
- Canvas 기반 차트라면 별도의 ARIA 이름이나 fallback 콘텐츠 필요

표와 차트 선택 기준, 범주·시리즈 수 제한과 파이 차트 제한은 위 문서의 기능을 바탕으로 Agent_ground 사용 목적에 맞게 정한 프로젝트 정책입니다. 외부 문서가 특정 제한 수치를 규정한다고 주장하지 않습니다.

## 5. 웹 접근성

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [Complex Images](https://www.w3.org/WAI/tutorials/images/complex/)
- [Images Tutorial](https://www.w3.org/WAI/tutorials/images/)
- [Contrast Minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
- [Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
- [Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color)

반영한 원칙:

- 일반 텍스트 대비 4.5:1, 큰 텍스트 대비 3:1을 목표로 함
- 의미 있는 UI와 그래픽은 인접 색상과 3:1 대비를 목표로 함
- 색상만으로 의미를 전달하지 않음
- 복잡한 차트는 짧은 설명과 상세 텍스트 또는 데이터 표를 함께 제공
- 장식 이미지는 빈 대체 텍스트, 정보 이미지는 목적에 맞는 대체 텍스트 제공

## 6. 검증되지 않은 항목

공식 문서를 읽었다고 해서 실제 Flow 실행이 검증된 것은 아닙니다. 현재 다음 항목은 미검증입니다.

- 사내 Vision 모델의 Base64 Data URL 처리 방식과 한도
- 표지·본문 다중 이미지의 실제 모델 이해 품질
- 한글이 포함된 참고 이미지의 디자인 추출 정확도
- Langflow `1.8.2` Builder에서 전체 JSON import 후 실행
- 실제 Renderer의 모든 브라우저·해상도·인쇄 결과
- Report API 게시와 사내 네트워크 공유

따라서 Flow 상태는 `user_testing`으로 유지합니다.
