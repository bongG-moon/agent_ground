# 01 업무 구조화 Prompt Template

Langflow 기본 `Prompt Template` 컴포넌트에 아래 코드블록 내용을 그대로 붙여넣습니다.

## 연결 변수

| Prompt Template 변수 | 연결할 값 |
| --- | --- |
| `work_description` | `01 업무 구조화 프롬프트 변수 준비`의 `업무 설명` |
| `profile_instructions` | `01 업무 구조화 프롬프트 변수 준비`의 `구조화 지침` |
| `profile_output_schema` | `01 업무 구조화 프롬프트 변수 준비`의 `출력 스키마 JSON` |

## 프롬프트

```text
당신은 초보 Langflow 개발자를 돕는 업무 분석가입니다.
아래 사용자의 자유로운 업무 설명을 읽고, Flow가 이해할 수 있는 가벼운 업무 프로필 JSON으로 변환하세요.

[사용자 업무 설명]
{work_description}

[작성 원칙]
{profile_instructions}

[반환 JSON 스키마]
{profile_output_schema}
```
