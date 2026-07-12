# 사내 업무용 Langflow Standalone Component 추천 ITEM LIST

> 조사 기준일: 2026-07-12  
> 대상 환경: Langflow `1.8.2`, LFX `0.3.4`  
> 구현 원칙: 실제 구현 시 Custom Component 하나가 Python 파일 하나로 동작하는 Standalone 방식  
> 현재 상태: **추천 후보 30종 중 1종 구현, 외부 참조 1종 추가 구현 — 모두 `user_testing`**

## 1. 문서 목적

이 문서는 회사에서 반복적으로 활용할 가능성이 높은 Custom Component 후보를 조사하고, 선택한 항목의 구현·검증 상태를 함께 관리하는 목록입니다.

- `추천·미구현`은 코드가 없는 후보이며 구현 완료나 동작 검증을 의미하지 않습니다.
- 사용자가 선택한 `multi_image_base64_encoder`는 실제 Langflow `1.8.2` Standalone Component로 구현했습니다.
- 다른 프로젝트에서 검증한 설계를 일반화한 `cached_named_run_flow_tool`도 공용 Component로 추가했습니다.
- 이번 범위에는 두 Component를 사용하는 별도 Flow JSON을 만들지 않습니다.
- 구현 후 사용자 실제 Builder 확인 전까지는 `user_testing`, 사용자가 완료를 승인한 뒤에만 `approved`로 전환합니다.

## 2. 선정 기준

| 기준 | 확인 내용 |
| --- | --- |
| 반복 사용성 | 한 Flow가 아니라 여러 부서·Agent에서 다시 쓸 수 있는가 |
| 기본 Node와 차별성 | Read File, API Request, Webhook, Loop 같은 Langflow 기본 기능을 단순 복제하지 않는가 |
| 비개발자 효용 | 코드 없이 toggle, dropdown, file upload 등으로 정책을 선택할 수 있는가 |
| 안전 경계 | 파일 크기, credential, 권한, 부분 실패, 중복 실행을 명확히 통제하는가 |
| Standalone 가능성 | 한 Python 파일로 등록하거나 의존성을 명확하게 분리할 수 있는가 |
| 검증 가능성 | 정상·빈 입력·잘못된 입력·초과 입력의 성공 기준을 자동 검사할 수 있는가 |

현재 프로젝트에는 데이터 조회·병합, HTML 리포트, 문서 RAG용 Component가 이미 있습니다. 아래 목록은 그 기능을 다시 만드는 대신 파일·Webhook·JSON 계약·배치·승인·감사처럼 여러 Flow 사이에서 반복되는 공통 경계를 중심으로 정리했습니다.

## 3. 선택 구현 현황

| Component ID | 선정 경로 | 핵심 역할 | 현재 상태 | 상세 문서 |
| --- | --- | --- | --- | --- |
| `multi_image_base64_encoder` | 추천 목록 P0에서 사용자 선택 | 여러 이미지를 입력 순서대로 Base64/Data URL 목록으로 변환 | `user_testing` | [`USAGE_GUIDE.md`](multi_image_base64_encoder/USAGE_GUIDE.md) |
| `cached_named_run_flow_tool` | `metadata_driven_v5/route_flow_v2` 참조 구현을 사용자 선택 | 정확한 이름으로 하위 Flow를 해석하고 graph cache와 compact Agent Tool schema 제공 | `user_testing` | [초보자 설명](cached_named_run_flow_tool/BEGINNER_GUIDE.md) · [사용 가이드](cached_named_run_flow_tool/USAGE_GUIDE.md) |

두 항목은 특정 Flow에 종속되지 않는 공용 Component입니다. Python 식별자는 Langflow 계약을 위해 영문으로 유지하고, Builder UI·안내·오류·주석·가이드는 한글로 작성했습니다.

## 4. 우선 추천 목록

### P0 · 외부 시스템 없이 먼저 만들기 좋은 공용 Component

| 순위 | 추천 ID | 실제 업무 활용 | 예상 입력 → 출력 | 추가 의존성 | 상태 |
| ---: | --- | --- | --- | --- | --- |
| 1 | `multi_image_base64_encoder` | 제품 사진, 불량 이미지, 전·후 사진, 증빙 이미지를 Vision API 요청에 사용 | 여러 이미지 파일 → 입력 순서가 보존된 Base64/Data URL 항목 목록 | 없음 | 구현·`user_testing` |
| 2 | `file_batch_manifest_builder` | 첨부파일 목록, 중복 업로드, 원문 전달 전 사전 점검 | 여러 파일 → 파일명·크기·MIME·SHA-256·중복 manifest | 없음 | 추천·미구현 |
| 3 | `webhook_payload_normalizer` | GitHub, Slack, 사내 시스템마다 다른 Webhook 구조 통일 | `payload`/`data`/`body` → 공통 Event envelope | 없음 | 추천·미구현 |
| 4 | `json_contract_validator` | LLM·API 결과가 사내 JSON 규격에 맞는지 결정론적으로 확인 | JSON + schema → valid/errors/warnings | 없음 또는 `jsonschema` | 추천·미구현 |
| 5 | `batch_payload_chunker` | 대량 목록을 Loop나 API 제한에 맞춰 분할 | 항목 목록 → 순서가 보존된 batch Data/DataFrame | 없음 | 추천·미구현 |
| 6 | `standard_result_envelope_builder` | 서로 다른 Component 결과 형식 통일 | 임의 결과 → success/data/errors/warnings/metadata | 없음 | 추천·미구현 |
| 7 | `payload_size_policy_gate` | LLM·API 전송 전에 텍스트·JSON·Base64 크기 차단 | payload + 목적별 제한 → allowed/blocked/size | 없음 | 추천·미구현 |
| 8 | `nested_sensitive_field_redactor` | token, password, cookie, API key가 LLM·로그로 전달되는 것 방지 | 중첩 JSON → 마스킹 JSON + redaction summary | 없음 | 추천·미구현 |
| 9 | `list_order_correlator` | Loop 전후 비동기 결과를 원래 요청 순서로 복원 | 원본 목록 + 결과 목록 → correlation 결과 | 없음 | 추천·미구현 |
| 10 | `batch_result_aggregator` | 성공·실패·skip·재처리 대상을 정확히 집계 | batch 결과 목록 → summary/retry items | 없음 | 추천·미구현 |

### P1 · 사내 API·승인·운영 업무에 유용한 Component

| 순위 | 추천 ID | 실제 업무 활용 | 핵심 설계 조건 | 상태 |
| ---: | --- | --- | --- | --- |
| 11 | `multimodal_payload_builder` | Base64 이미지 목록을 OpenAI, Gemini 또는 사내 Vision 요청 구조로 변환 | 공급자별 MIME·배열 순서·요청 크기 | 추천·미구현 |
| 12 | `webhook_signature_verifier` | 외부 Webhook이 실제 송신 시스템에서 온 것인지 확인 | raw body, HMAC-SHA256, timestamp, replay window, constant-time 비교 | 추천·미구현 |
| 13 | `api_pagination_collector` | `nextPageToken`, `@odata.nextLink`, `Link` 기반 여러 페이지 수집 | 최대 page/row, 동일 host, 429 처리 | 추천·미구현 |
| 14 | `enterprise_api_policy_wrapper` | 사내 API 호출에 허용 host·method·timeout·retry 정책 적용 | SSRF 방어는 서버 설정과 함께 적용 | 추천·미구현 |
| 15 | `deterministic_content_id_builder` | 문서·메일·Event 중복 후보와 안정 ID 생성 | canonicalization 규칙, SHA-256, version salt | 추천·미구현 |
| 16 | `approval_request_builder` | 비용·메일 발송·데이터 변경 전 사람 승인 요청 구성 | 요청자, 대상, 변경 diff, 위험도, 기한, request hash | 추천·미구현 |
| 17 | `approval_resume_verifier` | 승인 후 원래 요청과 실제 실행 인자가 같은지 확인 | 승인자, 만료, request hash, tamper 검증 | 추천·미구현 |
| 18 | `audit_event_builder` | 누가 무엇을 언제 실행했는지 표준 감사 Event 생성 | 원문·secret 비노출, actor/action/target/result | 추천·미구현 |
| 19 | `datetime_timezone_normalizer` | UTC/KST/epoch/문자열 날짜를 한 형식으로 통일 | timezone 명시, DST, 잘못된 날짜 처리 | 추천·미구현 |
| 20 | `tabular_schema_normalizer` | CSV·Excel·API마다 다른 column을 사내 표준명으로 변환 | alias, 필수 column, 충돌·누락 보고 | 추천·미구현 |
| 21 | `large_payload_sampler` | 큰 표를 LLM에 전부 보내지 않고 preview·통계로 축약 | 앞·뒤 sample, 전체 row 수, sample 한계 표시 | 추천·미구현 |

### P2 · 추가 패키지나 운영 인프라가 필요한 Component

| 순위 | 추천 ID | 실제 업무 활용 | 필요한 조건 | 상태 |
| ---: | --- | --- | --- | --- |
| 22 | `image_optimizer` | Vision 전송 전 resize·압축·EXIF 제거 | Pillow, decompression bomb 방어 | 추천·미구현 |
| 23 | `base64_file_decoder` | 이미지 생성 API의 Base64 응답을 파일로 복원 | strict decode, 최대 크기, 안전한 출력 경로 | 추천·미구현 |
| 24 | `idempotency_store_guard` | 결재·등록·메일·Webhook 중복 실행 차단 | Redis, MongoDB 또는 SQLite 같은 durable state | 추천·미구현 |
| 25 | `safe_archive_bundle_builder` | 여러 보고서·첨부물을 제한된 ZIP으로 구성 | zip-slip, 전체 크기, 민감 파일 정책 | 추천·미구현 |
| 26 | `malware_scan_adapter` | 업로드 파일을 사내 antivirus/sandbox로 검사 | 승인된 보안 서비스와 보존·반출 정책 | 추천·미구현 |
| 27 | `office_document_converter` | DOCX·PPTX·XLSX를 PDF·text·page image로 표준화 | LibreOffice 또는 사내 변환 서비스 | 추천·미구현 |
| 28 | `email_attachment_normalizer` | 메일 본문과 여러 첨부물을 공통 문서 입력으로 변환 | Mail connector, 파일 정책, 대화 thread ID | 추천·미구현 |
| 29 | `observability_event_emitter` | Flow 실행 시간·상태·오류 분류를 모니터링 시스템에 전달 | 로그 저장소, trace ID, 민감정보 정책 | 추천·미구현 |
| 30 | `human_task_queue_adapter` | Agent가 처리하지 못한 요청을 사람 업무함으로 전달 | 티켓/업무함 API, 소유자, SLA, 재개 token | 추천·미구현 |

## 5. 다중 이미지 Base64 인코더 구현 계약

구현 파일은 [`multi_image_base64_encoder.py`](multi_image_base64_encoder/multi_image_base64_encoder.py)입니다. 파일 하나만 Custom Component에 등록할 수 있습니다.

### 입력

| 입력 | 형식 | 기본값 |
| --- | --- | --- |
| `image_files` | 다중 `FileInput`, 업로드 순서 유지 | 필수 |
| `output_format` | `base64` / `data_url` | `base64` |
| `error_policy` | `reject_batch` / `skip_invalid` | `reject_batch` |
| `max_files` | 최대 이미지 수 | 20 |
| `max_file_size_mb` | 이미지 하나의 raw byte 제한 | 8 MB |
| `max_total_size_mb` | 전체 raw byte 제한 | 12 MB |
| `allow_svg` | 신뢰된 정적 SVG만 예외 허용 | `false` |

### 출력 예시

```json
{
  "success": true,
  "order_preserved": true,
  "items": [
    {
      "index": 0,
      "position": 1,
      "filename": "before.png",
      "mime_type": "image/png",
      "byte_size": 12034,
      "sha256": "...",
      "encoding": "base64",
      "value": "..."
    },
    {
      "index": 1,
      "position": 2,
      "filename": "after.jpg",
      "mime_type": "image/jpeg",
      "byte_size": 15382,
      "sha256": "...",
      "encoding": "base64",
      "value": "..."
    }
  ],
  "errors": [],
  "warnings": []
}
```

### 구현에 반영한 안전 조건

- 파일명을 정렬하지 않고 Langflow가 전달한 입력 순서를 그대로 유지합니다.
- 확장자나 Content-Type만 믿지 않고 실제 file signature를 확인합니다.
- Base64가 원본보다 약 33% 커지는 점을 고려해 원본 전체 제한 기본값을 보수적으로 적용합니다.
- SVG는 기본 거절하고, 필요하면 별도 안전 정책과 사용자 opt-in을 둡니다.
- Base64 본문, 전체 로컬 경로와 파일 내용을 status·error·log에 넣지 않습니다.
- `Data(data={"items": [...]})` 형태로 반환해 다음 Component가 안정적으로 연결하게 합니다.

## 6. 캐시된 이름 기반 Run Flow 도구 구현 계약

구현 파일은 [`cached_named_run_flow_tool.py`](cached_named_run_flow_tool/cached_named_run_flow_tool.py)입니다. 참고한 외부 원본과 설명을 그대로 복사하지 않고 다음 문제를 보완했습니다.

- Import 환경마다 달라지는 Flow ID를 export에 고정하지 않고 정확한 이름으로 다시 조회
- 기본 조회 범위를 부모 Router와 같은 Langflow 폴더로 제한
- 같은 범위의 동명 Flow가 0개 또는 2개 이상이면 실행 거절
- 현재 부모 Flow 자신을 하위 Flow로 직접 실행하는 재귀 차단
- 다른 폴더 조회는 고급 toggle을 켠 경우에만 현재 사용자 전체에서 고유 이름 확인
- 실제 `user_id + flow_id` 기준 graph cache와 `updated_at` 무효화
- 같은 초 안의 수정도 감지하도록 microsecond를 보존한 timestamp 비교
- Agent Tool schema의 `flow_tweak_data` 안에는 node ID가 없는 필수 `question` 하나만 노출
- 실행 직전에 현재 그래프의 유일한 Chat Input ID를 찾아 내부 `input_value` tweak로 변환
- provider가 `-`·`~`를 `_`로 바꾸는 Tool 인자 정규화 문제 차단
- 명시적 세션이 없으면 부모 graph session 상속
- Tool 이름·설명·세션 ID의 길이와 문자 검증
- `return_direct=true`일 때 하위 Flow 결과를 추가 Agent 재작성 없이 반환

이 Component는 기본 `RunFlowBaseComponent`의 실행 기능을 대체하지 않고 그대로 상속합니다. 별도로 만든 이유는 다른 환경에서 바뀌는 Flow ID 재해석, Agent Tool 입력 축소, 세션 상속과 결과 직접 반환 같은 Router·Standalone 배포 경계를 보완하기 위해서입니다. 초보자용 배경 설명은 [`cached_named_run_flow_tool/BEGINNER_GUIDE.md`](cached_named_run_flow_tool/BEGINNER_GUIDE.md)를 확인합니다.

질문이 없을 때 세션 이력이나 이전 분석 상태에서 복구하지 않습니다. 필수 `question`이 비어 있거나 대상 하위 Flow의 Chat Input이 정확히 하나가 아니면 하위 Flow 실행 전에 오류로 중단합니다. standalone 재import나 Chat Input 교체로 node ID가 바뀌면 현재 그래프에서 새 ID를 다시 확인합니다.

이 Component가 캐시하는 것은 Flow graph 구성뿐입니다. 데이터 조회, 코드 실행, LLM 응답과 최종 답변은 요청마다 다시 실행합니다. Langflow `1.8.2`의 process-local shared cache에 의존하므로 서버 재시작과 다중 worker 사이에서는 cache가 유지·공유되지 않습니다.

대상 하위 Flow에는 사용자 입력용 Chat Input과 Agent Tool용 최종 출력이 각각 정확히 하나 있어야 합니다. 자세한 연결·운영 조건은 [`cached_named_run_flow_tool/USAGE_GUIDE.md`](cached_named_run_flow_tool/USAGE_GUIDE.md)를 확인합니다.

## 7. 기능군별 선택 가이드

| 필요한 업무 | 먼저 검토할 ITEM |
| --- | --- |
| 여러 이미지·파일을 API에 보내기 | `multi_image_base64_encoder`, `file_batch_manifest_builder`, `payload_size_policy_gate`, `multimodal_payload_builder` |
| 외부 시스템 Event 받기 | `webhook_signature_verifier`, `webhook_payload_normalizer`, `idempotency_store_guard` |
| LLM/API 출력 안정화 | `json_contract_validator`, `standard_result_envelope_builder`, `nested_sensitive_field_redactor` |
| 대량 목록 처리 | `batch_payload_chunker`, `list_order_correlator`, `batch_result_aggregator` |
| 결재 후 실제 업무 실행 | `approval_request_builder`, `approval_resume_verifier`, `audit_event_builder` |
| 큰 표·문서 전처리 | `tabular_schema_normalizer`, `large_payload_sampler`, `office_document_converter` |
| 운영 모니터링·사람 이관 | `observability_event_emitter`, `human_task_queue_adapter` |

## 8. 기본 Component와 중복 구현하지 않을 항목

기능 차별점 없이 다음 항목은 새 Custom Component로 만들지 않습니다.

- 일반 파일 읽기: Langflow Read File
- 일반 HTTP 호출: Langflow API Request
- 일반 Webhook trigger: Langflow Webhook
- 단순 JSON key 선택·수정: JSON/Data Operations
- 일반 목록 반복: Langflow Loop
- LLM 기반 schema 추출: Structured Output
- 일반 표 정렬·필터: DataFrame/Table Operations

새 Component는 안전 정책, 순서 복구, 계약 검증, 승인 재개, 부분 실패 관리처럼 기본 Node에 없는 명확한 추가 가치가 있을 때만 구현합니다.

## 9. 웹 조사에서 확인한 설계 근거

- Langflow Custom Component는 입력·출력과 실행 method를 명시하며, 실제 설치 버전의 template 계약을 확인해야 합니다.
- Langflow Read File은 여러 파일을 처리할 수 있으므로 단순 파일 읽기보다 manifest·안전 검사·payload 변환에 집중하는 편이 유용합니다.
- Langflow Webhook은 외부 payload를 받지만 송신 서비스별 wrapper와 인증·서명 정책은 별도 처리가 필요합니다.
- Langflow Loop는 반복과 집계를 제공하므로 새 Component는 batch 크기, correlation, 부분 실패와 재처리 정보를 보강해야 합니다.
- OWASP는 업로드 파일에 대해 확장자 allowlist, Content-Type 불신, signature 검사, 크기 제한과 안전한 파일명을 함께 사용하도록 권고합니다.
- GitHub와 Slack은 Webhook 서명 검증 시 HMAC-SHA256, 원본 body, timestamp와 안전한 비교를 요구합니다.
- Microsoft Graph는 429와 `Retry-After`, 개별 batch 실패를 고려하도록 안내합니다.
- Power Automate의 실제 승인 예시는 송장, 작업 지시, 휴가와 출장처럼 사람 판단이 필요한 업무에 집중돼 있습니다.

## 10. 공식 참고자료

- [Langflow Custom Components](https://docs.langflow.org/components-custom-components)
- [Langflow Read File](https://docs.langflow.org/read-file)
- [Langflow Webhook](https://docs.langflow.org/webhook)
- [Langflow Loop](https://docs.langflow.org/loop)
- [Langflow JSON Operations](https://docs.langflow.org/1.9.0/data-operations)
- [Langflow API Request](https://docs.langflow.org/next/api-request)
- [Gemini Image Understanding](https://ai.google.dev/gemini-api/docs/image-understanding)
- [OpenAI Images and Vision](https://developers.openai.com/api/docs/guides/images-vision)
- [Python Base64](https://docs.python.org/3/library/base64.html)
- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)
- [GitHub Webhook Validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [Slack Request Verification](https://api.slack.com/docs/verifying-requests-from-slack)
- [Microsoft Graph Throttling](https://learn.microsoft.com/en-us/graph/throttling)
- [Power Automate Approvals](https://learn.microsoft.com/en-us/power-automate/modern-approvals)

## 11. 다음 진행 방식

1. 이 목록에서 실제로 필요한 Component ID를 선택합니다.
2. 선택한 업무 사례와 연결할 앞·뒤 Node를 확인합니다.
3. 해당 ITEM만 Standalone Component로 구현합니다.
4. 정상·빈 입력·오류·초과 입력을 Langflow `1.8.2`에서 검증합니다.
5. 사용자가 직접 확인한 뒤 완료 승인 시 HTML 설명서와 추천 자산에 반영합니다.

현재는 사용자가 선택한 두 Component를 구현하고 자동 검증하는 단계이며, 사용자 Builder 확인 전까지 `user_testing` 상태입니다.
