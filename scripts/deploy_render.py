"""Render.com 배포 트리거 — 현재 서비스의 최신 커밋으로 신규 Deploy 를 시작한다.

실행
----
    python scripts/deploy_render.py

필요 환경변수 (.env)
-------------------
    RENDER_API_KEY      Render 대시보드 → Account → API Keys 에서 발급
    RENDER_SERVICE_ID   srv-xxxxxxxx 형식. 서비스 URL 에서 확인
                        (예: https://dashboard.render.com/web/srv-abc123)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
_API_BASE = "https://api.render.com/v1"


def _load_env() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("RENDER_API_KEY", "").strip()
    service_id = os.environ.get("RENDER_SERVICE_ID", "").strip()
    if not api_key or not service_id:
        print("[오류] .env 에 아래 2개 변수를 추가한 뒤 다시 실행하세요:", file=sys.stderr)
        print("  RENDER_API_KEY=<Render 대시보드 → Account → API Keys>", file=sys.stderr)
        print("  RENDER_SERVICE_ID=<Render 서비스 URL 의 srv-xxxxxxxx>", file=sys.stderr)
        sys.exit(1)
    return api_key, service_id


def main() -> int:
    api_key, service_id = _load_env()

    url = f"{_API_BASE}/services/{service_id}/deploys"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        r = httpx.post(url, headers=headers, json={}, timeout=30.0)
    except Exception as exc:
        print(f"[오류] 네트워크: {exc}", file=sys.stderr)
        return 1

    if r.status_code >= 400:
        body = r.text.strip().replace("\n", " ")[:400]
        print(f"[오류] HTTP {r.status_code}: {body}", file=sys.stderr)
        return 1

    data = r.json() if r.content else {}
    deploy_id = data.get("id") or data.get("deployId") or "—"
    status = data.get("status") or "triggered"
    commit = (data.get("commit") or {}).get("id") or "—"

    print("[배포 트리거 완료]")
    print(f"  service_id : {service_id}")
    print(f"  deploy_id  : {deploy_id}")
    print(f"  status     : {status}")
    print(f"  commit     : {commit}")
    print()
    print("  대시보드   : "
          f"https://dashboard.render.com/web/{service_id}/deploys/{deploy_id}")
    print(f"  API 상태   : {_API_BASE}/services/{service_id}/deploys/{deploy_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
