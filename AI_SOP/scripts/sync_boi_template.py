from __future__ import annotations

import argparse
import json

from app.config import Settings
from app.template_manager import TemplateManager


def main() -> None:
    parser = argparse.ArgumentParser(description="BoI Wiki Local 템플릿을 검증 가능한 버전 저장소로 동기화합니다.")
    parser.add_argument("--activate", action="store_true", help="호환성 검사를 통과한 버전을 즉시 활성화")
    args = parser.parse_args()
    settings = Settings()
    manager = TemplateManager(
        runtime_root=settings.runtime_root,
        repository_url=settings.boi_template_repository,
        branch=settings.boi_template_branch,
        active_sha=settings.boi_template_active_sha,
        local_path=settings.boi_template_local_path,
    )
    result = manager.sync()
    if args.activate:
        if not result["isCompatible"]:
            raise SystemExit(f"호환되지 않는 템플릿입니다: {result['missingPaths']}")
        result["activation"] = manager.activate(str(result["commitSha"]))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
