# Agent Ground 저장소 안내

이 저장소는 서로 목적이 다른 작업을 최상위 폴더에서 분리합니다.

| 폴더 | 용도 | 평소 수정 대상 |
| --- | --- | --- |
| [`agent_ground/`](agent_ground/) | Langflow 1.9.2 기반 Component, Flow, 교육 포털과 개발 지침 | Agent Ground 기능을 만들거나 고칠 때 |
| [`agent_skill_hub/`](agent_skill_hub/) | Agent Skill을 수집·평가·내보내는 별도 Skill Hub 프로젝트 | Skill 카탈로그와 평가 체계를 다룰 때 |
| [`deliverables/`](deliverables/) | 이전 작업에서 만든 최종 PPT, 전달용 ZIP과 문서 산출물 | 완성 산출물을 확인할 때만 |

Agent Ground 개발을 시작할 때는 [`agent_ground/README.md`](agent_ground/README.md)를 먼저 읽습니다.  
각 내부 폴더의 이유와 수정 기준은 [`agent_ground/FOLDER_GUIDE.md`](agent_ground/FOLDER_GUIDE.md)에 정리되어 있습니다.

## 숨김 항목

- `.git/`: Git 이력과 브랜치를 보관하는 필수 폴더이므로 삭제하면 안 됩니다.
- `.gitignore`: 실행 중 생기는 임시 파일이 Git에 올라가지 않게 하는 규칙입니다.
- `.gitattributes`: 한글 문서와 소스의 줄바꿈·파일 형식을 일관되게 유지하는 규칙입니다.
- `.codex-remote-attachments/`: Codex 앱이 첨부파일을 임시 보관할 수 있는 로컬 폴더입니다. 프로젝트 소스가 아니며 Git에는 포함되지 않습니다.

Langflow 검증용 가상환경, 캐시와 테스트 임시 폴더는 필요할 때만 생성되며 Git에는 포함하지 않습니다.
