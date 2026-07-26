# Agent Ground 현재 검증 보고서

> 검증일: 2026-07-26  
> 기본 대상: Langflow `1.9.2`, langflow-base `0.9.2`, LFX `0.4.2`, Python `3.12.13`  
> 자산 상태: 실행 가능 자산은 `user_testing`, `reusable_data_flow`는 내용 불일치로 `building`

## 최종 결과

Agent Ground의 Component, Flow, 생성기, 교육자료와 지침을 Langflow 1.9.2 기준으로 이관했습니다. 버전 문자열만 바꾸지 않고 LFX 0.4.2의 실제 Component loader와 Langflow 1.9.2 Graph 계약으로 template과 edge handle을 다시 만들었습니다.

| 검사 | 결과 |
| --- | --- |
| 공개 기능 Component | 21개 |
| Flow 내부 Standalone Node | 33개 |
| Business Agent Design 전용 Standalone Node | 15개 |
| 1.9.2 실제 loader로 평가한 Python 원본 | 총 69개, 모두 통과 |
| Graph parser로 읽은 Flow JSON | 10개, 모두 통과 |
| Flow 합계 | 132 nodes / 145 edges |
| 전체 회귀 테스트 | 109개 통과 |
| 프로젝트 구조 검사 | JSON 83개, Python 108개, Flow manifest 7개 |
| 포털 검사 | HTML 128개, 로컬 링크 2,886개 통과 |
| Registry | Component 21개 + 최상위 Flow 7개 = 28개 |
| 전체 Import Bundle | 실행 가능 Flow 7개, BOM 없음, 안정된 순서 확인 |

## 1.9.2에서 확인한 핵심 계약

- 모든 실행 Node template의 `lf_version`을 `1.9.2`로 다시 생성했습니다.
- 모든 edge handle은 1.9.2 UI가 내보내는 `œ` quote delimiter 형식으로 생성하고 `edge.data`와 일치하는지 확인했습니다.
- LFX 0.4.2에서 화면 타입으로 추가된 `JSON`, `Table` alias를 template과 연결 계약에 반영했습니다.
- Python 원본에서는 호환 클래스 `Data`, `DataFrame`을 사용할 수 있으며, 설명 화면에서는 `JSON`, `Table`을 우선 표시합니다.
- Agent와 Language Model에는 특정 공급자 모델을 고정하지 않고 1.9.2의 통합 모델 선택기에서 조직 승인 모델을 선택하도록 했습니다.
- Custom Component 허용 정책과 `LANGFLOW_COMPONENTS_PATH` 운영 기준을 환경·교육 문서에 추가했습니다.
- 1.8.2에서 만든 Flow JSON의 built-in Node를 그대로 유지하지 않고 LFX 0.4.2 Component index 기준 template으로 교체했습니다.

## Flow별 검증 결과

| Flow JSON | Node | Edge | 상태 | 비고 |
| --- | ---: | ---: | --- | --- |
| `reusable_data_flow.json` | 16 | 21 | `building` | 1.9.2 schema·Graph 해석은 통과했지만 내용이 문서의 데이터 조회 Flow와 달라 전체 Bundle에서 제외 |
| `html_report_flow.json` | 18 | 22 | `user_testing` | 1.9.2 template·handle 이관 |
| `enterprise_document_rag_flow.json` | 13 | 10 | `user_testing` | 기능 Component 6개 + 내부 Node 3개 |
| `meeting_action_skill_flow.json` | 5 | 3 | `user_testing` | 회의 Skill 하위 Flow |
| `skill_based_agent_flow.json` | 9 | 8 | `user_testing` | 직접 계산 Tool 2개 + 안전한 Run Flow Tool 1개 |
| `ppt_reference_html_flow.json` | 17 | 22 | `user_testing` | 참고 이미지·데이터 기반 HTML 프레젠테이션 |
| `mail_attachment_summary_flow.json` | 14 | 12 | `user_testing` | EWS·DRM·Vision 운영 경로 |
| `mail_attachment_summary_dummy_flow.json` | 14 | 12 | 테스트 전용 | 외부 메일함 없이 typed pipeline 검증 |
| `drm_document_text_extraction_flow.json` | 2 | 1 | `user_testing` | 직접 업로드 문서 추출 최소 Flow |
| `business_agent_design_complete.json` | 24 | 34 | `user_testing` | BEFORE/AFTER 분기형 Flow Chart와 개선 설명 |

## 기능 회귀에서 확인한 내용

### Component와 데이터 타입

- 21개 공개 Component는 Standalone 한 파일로 template 생성에 성공했습니다.
- Oracle, H-API, Datalake, GooDocs, 일반 API 조회 Component는 `data_table` 한 개만 출력합니다.
- 1.9.2 template에서는 표 출력이 `DataFrame`, `Table` alias를 함께 제공하는지 확인했습니다.
- HTML 프레젠테이션 결과는 `Data`, `JSON` alias를 함께 제공하는지 확인했습니다.
- 메일 파일 Reader의 Builder 표시 출력은 `Table`, Formatter 입력은 `DataFrame`, `Table` 호환 계약으로 확인했습니다.

### Run Flow와 Skill Agent

- 외부 Tool schema는 내부 Node ID를 노출하지 않고 `flow_tweak_data.question` 하나를 사용합니다.
- 실행 시 현재 하위 Flow의 유일한 Chat Input ID를 찾아 내부 `~input_value` key로 변환합니다.
- 경비·휴가 계산은 개별 Component Tool, 회의 액션아이템은 이름 기반 Run Flow Tool로 구성했습니다.
- 상위 Skill Flow는 7 vertices / 8 edges, 회의 하위 Flow는 4 vertices / 3 edges로 실제 LFX Graph 해석을 통과했습니다.

### 문서·메일·프레젠테이션

- DOCX, PPTX, XLSX, TXT 로컬 추출과 DRM transport 무호출 경로를 회귀 테스트했습니다.
- Office/PDF 테스트 의존성은 `environment/langflow-1.9.2-validation-requirements.txt`에 고정했습니다.
- 메일 운영 Flow와 dummy Flow 모두 14 nodes / 12 edges이며 `JSON`, `Table` typed pipeline을 확인했습니다.
- PPT Flow는 특정 모델명을 고정하지 않고 승인된 멀티모달 모델을 사용하도록 수정했습니다.
- 발표 데이터의 표·KPI·막대·선·산점도 선택, dataset·column 검증, HTML escaping과 외부 URL 차단을 확인했습니다.

### 교육·포털·지침

- 교육 포털에 1.9.2 기본 환경, `JSON`·`Table`, 통합 모델 선택기와 Custom Component 정책을 반영했습니다.
- 1.8.2에서 발견한 RAG 문제 문서는 삭제하지 않고 `과거 해결 기록`으로 명확히 표시했습니다.
- 현재 이관 방법은 `training/references/LANGFLOW_1_9_2_MIGRATION_GUIDE.md`와 HTML 문제 해결 페이지에서 확인할 수 있습니다.
- Master Guide와 이식 가능한 Skill 4종의 기본 구현 기준을 1.9.2 / 0.9.2 / 0.4.2로 변경했습니다.
- 저장소를 `agent_ground/`, `agent_skill_hub/`, `deliverables/`로 분리하고 `FOLDER_GUIDE.md`에 각 폴더의 책임을 기록했습니다.

## 자동 검사 명령

Agent Ground 폴더에서 Langflow 1.9.2 격리 Python을 사용해 다음 검사를 수행했습니다.

```powershell
python -m pytest -q
python scripts/validate_project.py
python scripts/validate_langflow_1_9_2_runtime.py
```

생성기 `--check`도 다음 자산에 대해 통과했습니다.

- Enterprise Document RAG Flow
- Skill 기반 Agent 상위·하위 Flow와 Project Bundle
- PPT 참고 이미지 HTML 프레젠테이션 Flow
- EWS·DRM 메일 요약 운영·dummy Flow
- DRM 문서 텍스트 추출 Flow

## 경고와 검증 경계

- 테스트 중 나온 Pydantic·LangChain deprecation warning은 Langflow 1.9.2 의존 패키지 내부 경고이며 Agent Ground 회귀 실패는 아닙니다.
- PyTorch는 설치하지 않았습니다. 이번 자산은 로컬 Transformers 모델을 실행하지 않으며, 실제 LLM은 Builder에서 승인된 API·vLLM 모델을 연결합니다.
- 실제 사내 Oracle, H-API, Datalake, GooDocs, EWS, DRM endpoint와 운영 계정은 호출하지 않았습니다.
- 실제 Builder UI Import, 조직의 Custom Component 정책, 모델별 Tool Calling과 Vision 응답은 사용자 환경에서 확인해야 합니다.
- `reusable_data_flow`는 schema 호환성과 Graph 해석만 통과했습니다. 기능 내용이 맞지 않으므로 정상 실행 Flow로 간주하지 않습니다.

## 사용자 환경에서 남은 확인

- [ ] Langflow 1.9.2 Builder에 전체 7개 Flow Bundle을 Import하고 Canvas가 정상 표시되는지 확인
- [ ] 실제 사용할 공개 Component를 등록하고 한글 입력·출력 이름과 `JSON`·`Table` 포트 확인
- [ ] 회사 승인 Tool Calling 모델로 Skill 선택, 비대상 질문과 복합 질문 확인
- [ ] Cached Named Run Flow의 cold/warm 실행, session 상속과 하위 Flow 재import 후 질문 전달 확인
- [ ] 승인된 Vision 모델로 PPT 참고 이미지와 EWS JPG/JPEG 분석 확인
- [ ] 실제 사내 데이터·메일·DRM endpoint와 최소 권한 계정으로 연결 시험
- [ ] 포털을 데스크톱과 모바일에서 최종 시각 확인
- [ ] 올바른 `reusable_data_flow` export 제공 또는 신규 재구축
- [ ] 사용자 완료 승인 후 해당 자산만 `approved`로 전환

정적 검사와 격리 런타임 검사가 통과했더라도 위 사용자 환경 확인 전에는 운영 완료로 표시하지 않습니다.
