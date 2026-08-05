from __future__ import annotations

import json

from app.config import Settings
from app.storage import MongoStore


def main() -> None:
    settings = Settings()
    if settings.ai_sop_demo_mode:
        raise SystemExit("메모리 기반 체험 모드에서는 정리할 MongoDB 초안이 없습니다.")
    store = MongoStore(settings.mongodb_uri, settings.mongodb_database, settings.ai_sop_draft_retention_days)
    store.ping()
    print(json.dumps(store.cleanup_expired_private_data(), ensure_ascii=False))


if __name__ == "__main__":
    main()
