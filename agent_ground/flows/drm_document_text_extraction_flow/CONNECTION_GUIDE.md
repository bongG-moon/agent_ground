# DRM 문서 텍스트 추출 Flow 연결 가이드

## 캔버스

```text
01 문서 텍스트 추출 (DRM 자동)
  └─ extracted_text (Message)
       -> 02 추출 텍스트 출력.input_value (Message)
```

## 실제 연결표

| 순서 | From 노드 | output | 타입 | To 노드 | input | 타입 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `DrmDocumentTextExtractor-drmDocument` | `extracted_text` | `Message` | `ChatOutput-drmDocumentText` | `input_value` | `Message` |

## 실행 경계

- 파일 업로드와 처리 모드는 `01 문서 텍스트 추출 (DRM 자동)`에 직접 입력합니다.
- DRM 호출 가능성이 있는 모드에서만 API 주소, 인증값과 사번이 필요합니다.
- `01`이 로컬 추출 결과와 DRM API 응답을 원래 파일 순서대로 합친 `Message`를 만듭니다.
- `02`는 내용을 변경하지 않고 Chat Output으로 표시합니다.
- LLM, Agent, MCP, Run Flow, 외부 저장 노드는 포함하지 않습니다.
