"""Local-dev helpers for thelake auth: provision a workspace and mint an assertion JWT.

thelake verifies an HS256 `X-Softprobe-Assertion` (iss=softprobe-edge, aud=sp-backend)
signed with SOFTPROBE_ASSERTION_HMAC_SECRET and binds the DuckLake scope from the
`workspace_id` claim. This mirrors what Softprobe's Explorer gateway does in hosted mode.

  python thelake_auth.py provision     # once per workspace (admin key)
  export THELAKE_TOKEN=$(python thelake_auth.py token)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

import requests

BASE = os.environ.get("THELAKE_URL", "http://localhost:8090")
WORKSPACE_ID = os.environ.get("THELAKE_WORKSPACE_ID", "5a7d3e9c-2b1f-4c8e-9a6d-0e4b7f2c1d83")
SECRET = os.environ.get("SOFTPROBE_ASSERTION_HMAC_SECRET", "dev-assertion")
ADMIN_KEY = os.environ.get("SOFTPROBE_ADMIN_API_KEY", "dev-admin")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_token(ttl_s: int = 6 * 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {"iss": "softprobe-edge", "aud": "sp-backend", "sub": "agent-qa-local",
              "workspace_id": WORKSPACE_ID, "exp": int(time.time()) + ttl_s}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    sig = hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


def provision(data_path: str) -> dict:
    r = requests.post(f"{BASE}/v1/workspaces", headers={"Authorization": f"Bearer {ADMIN_KEY}"}, json={
        "workspaceId": WORKSPACE_ID,
        "storageHints": {"ducklakeMetadataSchema": "ws_larkspur_demo", "ducklakeDataPath": data_path}})
    r.raise_for_status()
    return r.json() if r.content else {}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "token"
    if cmd == "provision":
        path = sys.argv[2] if len(sys.argv) > 2 else os.path.abspath("lake-data") + "/"
        print(provision(path))
    else:
        print(mint_token())
