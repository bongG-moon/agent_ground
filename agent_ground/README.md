# Agent Ground

코딩이 익숙하지 않은 사용자도 Agent Builder(Langflow)에서 업무용 AI Agent를 만들 수 있도록 교육자료, 기능 단위 Component, 재사용 Flow, 문제 해결 기록을 한곳에서 제공하는 프로젝트입니다.

저장소 전체 구조와 `agent_skill_hub`, 전달 산출물의 구분은 상위 [`../README.md`](../README.md)에서 확인할 수 있습니다. 이 폴더 안의 각 디렉터리 역할은 [`FOLDER_GUIDE.md`](FOLDER_GUIDE.md)에 정리되어 있습니다.

이 프로젝트에서 **Standalone은 한 파일로 등록하는 포장 방식**이고, **Component는 Flow 밖에서도 직접 재사용할 수 있는 기능 단위**입니다. Flow에 들어가는 Python 파일이라는 이유만으로 Component로 등록하지 않으며, 특정 Flow에 종속된 변환·프롬프트 조립·데모·출력 포장 단계는 `flows/<flow_id>/nodes/`의 내부 노드로 관리합니다.

## 기본 실행 기준

앞으로 별도 지시가 없으면 모든 Component, Flow, 생성기와 교육자료는 **실제 사내 구동 환경인 Langflow 1.9.2 / langflow-base 0.9.2 / LFX 0.4.2 / Python 3.12**를 기준으로 구현하고 수정합니다. 개발 PC의 Langflow Desktop이 1.10.2처럼 더 높은 버전이어도 대상 자산 생성 기준으로 사용하지 않으며, 프로젝트 전용 1.9.2 격리 환경에서 template 로딩과 Flow Graph 해석을 확인합니다.

Langflow 1.9 화면에서는 기존 `Data`와 `DataFrame`이 주로 `JSON`과 `Table`로 표시됩니다. Standalone Python 코드의 호환 클래스명은 여전히 `Data`와 `DataFrame`일 수 있으므로 문서에서 화면 타입과 Python 내부 타입을 구분합니다.

## 현재 구현 범위

- 새로 설계한 통합 교육 포털
- `reusable_data_flow` 재구축용 12개 Flow 내부 노드와 연결 설계 (현재 export 불일치로 Flow import 중단)
- 기존 `html_report_flow` 기반 Flow, 재사용 Component 3개와 Flow 내부 노드 6개
- 신규 `enterprise_document_rag_flow`, 재사용 Component 6개와 Flow 내부 노드 3개
- 신규 `skill_based_agent_flow`
  - Flow 내부 데모 Skill 카탈로그와 동적 Agent 지침
  - 경비 사전 점검·휴가 평일 계산·회의 액션아이템 구조화를 각각 Standalone Component Tool로 직접 연결
  - 실제 환경에서 오류가 있던 이름 기반 Run Flow Tool과 회의 하위 Flow는 제거
- 신규 `ppt_reference_html_flow`
  - 표지·본문 이미지는 Base64 Data URL로 변환한 뒤 Vision 모델이 디자인 규칙만 관찰
  - `02 발표 요청 정리`에서 제목·부제·목적·청중·톤·목차·마지막 요청·본문을 실제 Builder 필드로 직접 입력
  - 기존 표지·본문 `Multi Image Base64 Encoder`를 업로드 양식으로 사용하고 16:9 표지 1장·본문 2장 샘플 제공
  - 발표 brief와 실제 dataset을 사실 근거로 사용하고 표·KPI·막대·선·산점도를 계약에 따라 선택
  - 검증된 계획을 외부 CDN 없는 16:9 HTML 슬라이드로 렌더링하고 품질 Gate를 통과한 결과만 출력
- 신규 `mail_attachment_summary_flow`
  - 사내 EWS와 NTLM 인증으로 최근 Outlook 메일 본문·파일 첨부를 읽고, 첨부를 사내 DRM 어댑터로 처리
  - Microsoft Graph·Outlook Connector 같은 외부 Tool은 사용하지 않고 EWS SOAP 조회와 DRM 경계만 Flow 전용 내부 노드로 구성
  - 이후 `Read File → Loop → Parser → Language Model` 경로로 메일별 분석과 전체 업무 요약 생성
- 신규 `drm_document_text_extraction_flow`
  - PDF·PowerPoint·Excel·Word 파일을 직접 업로드하는 최소 Flow
  - 허용된 DRM text API에만 원본을 전송하고 반환 평문을 LLM 없이 Chat Output으로 출력
  - endpoint·토큰·사번·업로드 경로는 배포 JSON 기본값에서 제외
- 신규 `meeting_minutes_writer_flow`
  - 과거 녹취 TXT와 실제 작성 회의록을 업로드 순서대로 1:1 비교해 사용자의 선택·생략·구성·문장 스타일을 분석
  - 현재 녹취 TXT만 사실 근거로 사용하고, “일정 위주로 작성”, “잡담 제외” 같은 추가 지시를 학습 스타일보다 우선 적용
  - 과거 회의의 사실이 새 회의록에 섞이지 않도록 스타일 분석·초안 작성·사실 검토를 분리하고 최종 사람 검토를 필수로 표시
  - 실제 회의록이 Word·DRM 문서이면 기존 `drm_document_text_extractor`를 `자동(로컬 우선)` 모드로 재사용
- 독립 사용 사례와 입출력 계약을 갖춘 공용·업무·RAG·HTML·프레젠테이션 Component 20개
- 사내 공용 Standalone Component 추천 후보 30종과 선택 구현 자산
  - `multi_image_base64_encoder`
  - `drm_document_text_extractor`
- 기존 유연 조회 Flow와 분리한 최소 단위 직접 데이터 조회 Component 5종
  - `oracle_table_query`
  - `h_api_table_request`
  - `datalake_table_query`
  - `goodocs_table_reader`
  - `simple_api_table_request`
- 상위 Flow들의 사용자용 HTML 설명서
- Component manifest와 통합 registry
- 다른 PC로 복사해 바로 적용할 수 있는 Agent Ground 개발 Skill 4종
- Business Agent Design 24-node 실행 Flow, 공용 Library와 분리된 전용 Standalone 실행 Node 15개, Import Bundle

현재 실행 가능한 Flow와 Component 상태는 `user_testing`입니다. Flow 내부 노드는 소유 Flow 상태를 따르며 별도 공개 자산이나 registry 항목으로 세지 않습니다. `reusable_data_flow`는 실제 JSON이 과거 업무 설계 Flow로 확인되어 `building`으로 낮추고 전체 Bundle에서 제외했습니다. 사용자가 실제 Agent Builder 환경에서 확인하고 완료를 승인한 뒤에만 `approved`로 전환합니다.

공용 Component 추천 30종 중 `multi_image_base64_encoder`를 선택해 구현했습니다. 메일 첨부 처리에 사용하던 `drm_document_text_extractor`는 메일 Flow와 분리해도 쓸 수 있는 독립 기능이므로 별도 Component 가이드와 초보자 교육자료를 제공합니다. 이 Component는 DRM이 해제된 원본 파일을 배포하는 도구가 아니라, 일반 문서는 로컬에서 읽고 보호 문서는 승인된 사내 DRM text API를 통해 **평문 텍스트 또는 임시 TXT**로 변환하는 도구입니다. 또한 `reusable_data_flow`의 다중 요청·라우팅 설계와 12개 Flow 내부 Python 원본을 보존하면서 Oracle, H-API, Datalake, GooDocs와 일반 JSON API를 각각 한 번 조회하는 최소 단위 Component를 새 ID로 분리했습니다. 새 자산은 모두 `user_testing`입니다.

`skill_based_agent_flow`는 Langflow가 `SKILL.md`를 자동 탐색한다고 가정하지 않습니다. LLM에는 `expense_precheck_skill`, `leave_policy_skill`, `meeting_action_skill` 세 Tool 이름이 보이며 세 기능 모두 개별 Standalone Component를 직접 호출합니다. Flow 이름·내부 node ID·하위 Flow import 상태에 의존하지 않도록 구성했고, 승인·저장·메일 발송과 같은 외부 변경 Tool은 예제에 포함하지 않았습니다.

`ppt_reference_html_flow`에서는 참고 이미지를 내용 근거가 아닌 디자인 근거로만 사용합니다. 이미지 속 문구·숫자·지시사항은 신뢰하지 않고, 발표 내용과 차트 값은 사용자가 입력한 brief·dataset에서만 가져옵니다. `02 발표 요청 정리` Node에는 발표 제목, 발표 부제, 발표 목적, 대상 청중, 발표 언어, 발표 톤, 슬라이드 목차, 마지막 요청·의사결정, 발표 본문, 목표 슬라이드 수를 나눠 입력하는 실제 Builder 양식이 있습니다. 이미지는 기존 표지·본문 `Multi Image Base64 Encoder`에서 업로드하며, 샘플은 `reference_cover_navy_teal.png` 한 장과 `reference_body_trend.png`, `reference_body_comparison_table.png` 두 장입니다. LLM은 HTML을 직접 작성하지 않고 디자인 관찰 JSON과 슬라이드 계획 JSON만 제안합니다. Hallmark식 구성 원칙과 Emil식 모션 기준은 별도 `design_policy` Node, Python Normalizer, 결정론적 `html_presentation_renderer`, Quality Gate가 함께 강제합니다.

`meeting_minutes_writer_flow`는 과거 녹취와 실제 회의록의 대응 관계에서 사용자 스타일만 학습합니다. 과거 회의 내용은 현재 작성 단계에 전달하지 않고, 현재 녹취를 유일한 사실 근거로 사용합니다. 사용자가 Chat Input에 넣는 포함·제외 지시는 학습된 스타일보다 우선하며, 담당자·일정·결정사항은 최종 출력 전에 현재 녹취와 다시 비교합니다. 자동 생성 결과는 초안이므로 실제 배포·공유 전 사람이 반드시 검토해야 합니다.

## 바로 열기

- 통합 포털: [`html/index.html`](html/index.html)
- 전체 교육자료: [`html/training/index.html`](html/training/index.html)
- 초보자 학습 안내: [`html/training/overview.html`](html/training/overview.html)
- Flow 목록: [`html/flows/index.html`](html/flows/index.html)
- Component 목록: [`html/components/index.html`](html/components/index.html)
- Component 카탈로그 범위 기준: [`components/COMPONENT_CATALOG_SCOPE.md`](components/COMPONENT_CATALOG_SCOPE.md)
- 직접 데이터 조회 Component 포털: [`html/components/direct-data-access/index.html`](html/components/direct-data-access/index.html)
- 직접 데이터 조회 Component 통합 가이드: [`components/DIRECT_DATA_ACCESS_COMPONENTS_GUIDE.md`](components/DIRECT_DATA_ACCESS_COMPONENTS_GUIDE.md)
- 사내 공용 Component 추천 포털: [`html/components/enterprise-utility/index.html`](html/components/enterprise-utility/index.html)
- 사내 공용 Component 웹 조사·ITEM 목록: [`components/ENTERPRISE_UTILITY_COMPONENT_ITEM_LIST.md`](components/ENTERPRISE_UTILITY_COMPONENT_ITEM_LIST.md)
- 다중 이미지 Base64 인코더 가이드: [`components/multi_image_base64_encoder/USAGE_GUIDE.md`](components/multi_image_base64_encoder/USAGE_GUIDE.md)
- DRM 문서 텍스트 추출 초보자 교육: [`components/drm_document_text_extractor/BEGINNER_GUIDE.md`](components/drm_document_text_extractor/BEGINNER_GUIDE.md)
- DRM 문서 텍스트 추출 연결·운영 가이드: [`components/drm_document_text_extractor/USAGE_GUIDE.md`](components/drm_document_text_extractor/USAGE_GUIDE.md)
- Business Agent Design 설계: [`business_agent_design/BUSINESS_AGENT_DESIGN_IMPLEMENTATION_SPEC.md`](business_agent_design/BUSINESS_AGENT_DESIGN_IMPLEMENTATION_SPEC.md)
- 사내 Agent Flow/Component 수요 조사: [`business_agent_design/ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md`](business_agent_design/ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md)
- Business Agent Design 개별 Import: [`business_agent_design/flow/business_agent_design_complete.json`](business_agent_design/flow/business_agent_design_complete.json)
- 사내 문서 RAG 가이드: [`flows/enterprise_document_rag_flow/README.md`](flows/enterprise_document_rag_flow/README.md)
- 사내 문서 RAG 개별 Import: [`flows/enterprise_document_rag_flow/enterprise_document_rag_flow.json`](flows/enterprise_document_rag_flow/enterprise_document_rag_flow.json)
- Skill 기반 Agent 가이드: [`flows/skill_based_agent_flow/README.md`](flows/skill_based_agent_flow/README.md)
- Skill 기반 Agent 일괄 Import: [`flows/skill_based_agent_flow/00_SKILL_BASED_AGENT_ALL_FLOWS.json`](flows/skill_based_agent_flow/00_SKILL_BASED_AGENT_ALL_FLOWS.json)
- Skill 기반 Agent 상위 Flow: [`flows/skill_based_agent_flow/skill_based_agent_flow.json`](flows/skill_based_agent_flow/skill_based_agent_flow.json)
- PPT 참조 이미지 HTML 프레젠테이션 가이드: [`flows/ppt_reference_html_flow/README.md`](flows/ppt_reference_html_flow/README.md)
- PPT 참조 이미지 HTML 프레젠테이션 개별 Import: [`flows/ppt_reference_html_flow/ppt_reference_html_flow.json`](flows/ppt_reference_html_flow/ppt_reference_html_flow.json)
- EWS·DRM Outlook 메일 요약 가이드: [`flows/mail_attachment_summary_flow/README.md`](flows/mail_attachment_summary_flow/README.md)
- EWS·DRM Outlook 메일 요약 개별 Import: [`flows/mail_attachment_summary_flow/mail_attachment_summary_flow.json`](flows/mail_attachment_summary_flow/mail_attachment_summary_flow.json)
- DRM 문서 텍스트 추출 Flow 가이드: [`flows/drm_document_text_extraction_flow/README.md`](flows/drm_document_text_extraction_flow/README.md)
- DRM 문서 텍스트 추출 개별 Import: [`flows/drm_document_text_extraction_flow/drm_document_text_extraction_flow.json`](flows/drm_document_text_extraction_flow/drm_document_text_extraction_flow.json)
- 사용자 스타일 기반 회의록 Flow 가이드: [`flows/meeting_minutes_writer_flow/README.md`](flows/meeting_minutes_writer_flow/README.md)
- 사용자 스타일 기반 회의록 개별 Import: [`flows/meeting_minutes_writer_flow/meeting_minutes_writer_flow.json`](flows/meeting_minutes_writer_flow/meeting_minutes_writer_flow.json)
- 회의록 Flow 샘플과 기대 결과: [`flows/meeting_minutes_writer_flow/samples/EXPECTED_RESULT.md`](flows/meeting_minutes_writer_flow/samples/EXPECTED_RESULT.md)
- Langflow 실제 Builder 입력 양식·업로드 순서: [`flows/ppt_reference_html_flow/samples/INPUT_FORM.md`](flows/ppt_reference_html_flow/samples/INPUT_FORM.md)
- 16:9 샘플 표지 이미지: [`flows/ppt_reference_html_flow/samples/reference_images/reference_cover_navy_teal.png`](flows/ppt_reference_html_flow/samples/reference_images/reference_cover_navy_teal.png)
- 16:9 샘플 추세 본문 이미지: [`flows/ppt_reference_html_flow/samples/reference_images/reference_body_trend.png`](flows/ppt_reference_html_flow/samples/reference_images/reference_body_trend.png)
- 16:9 샘플 비교·표 본문 이미지: [`flows/ppt_reference_html_flow/samples/reference_images/reference_body_comparison_table.png`](flows/ppt_reference_html_flow/samples/reference_images/reference_body_comparison_table.png)
- 발표 데이터 입력 예시: [`flows/ppt_reference_html_flow/samples/sample_presentation_data.json`](flows/ppt_reference_html_flow/samples/sample_presentation_data.json)
- 발표 디자인·모션 정책: [`flows/ppt_reference_html_flow/references/DESIGN_MOTION_POLICY.md`](flows/ppt_reference_html_flow/references/DESIGN_MOTION_POLICY.md)
- Agent Ground 실행 가능 7개 Flow 일괄 Import: [`flows/00_AGENT_GROUND_ALL_FLOWS.json`](flows/00_AGENT_GROUND_ALL_FLOWS.json)
- 재사용 데이터 Flow export 불일치 기록: [`html/troubleshooting/reusable-data-flow-export-mismatch.html`](html/troubleshooting/reusable-data-flow-export-mismatch.html)
- 이동식 개발 Skill 묶음: [`skills/skill-pack.json`](skills/skill-pack.json)
- 프로젝트 기준: [`AGENT_GROUND_PROJECT_MASTER_GUIDE.md`](AGENT_GROUND_PROJECT_MASTER_GUIDE.md)
- 현재 검증 결과: [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md)
- Langflow 1.9.2 이관 가이드: [`training/references/LANGFLOW_1_9_2_MIGRATION_GUIDE.md`](training/references/LANGFLOW_1_9_2_MIGRATION_GUIDE.md)

## 폴더

| 폴더 | 역할 |
| --- | --- |
| `components/` | 독립 사용 사례와 안정된 입출력 계약을 가진 기능 단위 Component 원본 |
| `flows/` | Flow JSON, 연결 가이드, 샘플, 참고문서와 `nodes/`의 Flow 내부 Standalone Python 노드 |
| `training/` | 이후 확장할 교육 원본과 샘플 |
| `html/` | 브라우저에서 여는 통합 포털과 모든 HTML 설명서 |
| `registry/` | 자산 상태와 문서 경로의 기준 데이터 |
| `business_agent_design/` | 업무 Agent 설계 실행 Flow, 전용 Standalone Component, Prompt, 테스트와 문서 |
| `scripts/` | manifest·registry·HTML 생성 및 검증 도구 |
| `skills/` | 다른 PC의 Codex/Agent Skill 경로로 폴더째 복사할 수 있는 개발 규칙 4종과 설치 스크립트 |
| `environment/` | Langflow 1.9.2 기준 버전과 선택 의존성 설명 |
| `tests/` | 여러 자산에 공통 적용하는 회귀 테스트 |

더 자세한 수정 기준은 [`FOLDER_GUIDE.md`](FOLDER_GUIDE.md)를 확인합니다. `scripts/`는 Langflow Builder에 등록하는 Component가 아니라 Flow JSON·manifest·HTML을 생성하고 검증하는 개발 자동화 도구입니다.

## 기본 작업 순서

```text
구현
-> 로컬 검사
-> user_testing
-> 사용자 실제 환경 확인
-> 실패 기록과 수정
-> 사용자 완료 승인
-> approved 및 정식 포털/추천 카탈로그 반영
```

기존 `langflow교육자료`와 `기능flow` 폴더는 이관 출처로만 사용했으며 수정하지 않았습니다.

## 다른 환경에서 이어서 개발하기

저장소의 `skills/` 폴더를 함께 동기화합니다. Codex 사용자 Skill 경로에 설치하려면 프로젝트 루트에서 다음을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File skills/install.ps1
```

기본 목적지는 `$CODEX_HOME/skills`이며 `CODEX_HOME`이 없으면 `$HOME/.codex/skills`입니다. 기존 Skill은 자동으로 덮어쓰지 않으며, 의도적으로 갱신할 때만 `-Force`를 사용합니다.

| Skill | 역할 |
| --- | --- |
| `maintain-agent-ground` | 프로젝트 구조, 승인 상태, 기준 원본과 집·회사 환경 간 병합 |
| `build-langflow-standalone-component` | Component 승격 판단과 한글 Standalone 구현·계약·검증 |
| `build-langflow-flow-package` | Flow JSON, Component 참조, 내부 노드, Run Flow 안전 계약과 bundle |
| `maintain-agent-ground-portal` | registry 기반 포털, 교육자료, 계약·코드 화면과 반응형 QA |
