"""Repro: scores timestamped after a session's last span are missing from GET /v1/sessions/{id}.

Needs a running thelake with data from run_traffic.py and THELAKE_TOKEN set.

  python scripts/repro_session_score_window.py sess-0011
"""

import datetime as dt
import os
import sys
import time
import uuid

import requests

BASE = os.environ.get("THELAKE_URL", "http://localhost:8090")
H = {"X-Softprobe-Assertion": os.environ["THELAKE_TOKEN"]}
session = sys.argv[1] if len(sys.argv) > 1 else "sess-0011"

r = requests.post(f"{BASE}/v1/query/sql", headers=H, json={
    "sql": f"SELECT max(end_timestamp)::VARCHAR FROM traces WHERE session_id = '{session}'"}).json()
end = dt.datetime.fromisoformat(r["rows"][0][0][:26]).replace(tzinfo=dt.timezone.utc)
run = uuid.uuid4().hex[:6]

offsets = {"at_session_end": dt.timedelta(0), "end_plus_10min": dt.timedelta(minutes=10),
           "end_plus_2h": dt.timedelta(hours=2)}
for label, delta in offsets.items():
    resp = requests.post(f"{BASE}/v1/scores", headers=H, json={
        "score_id": f"repro-{run}-{label}", "timestamp": (end + delta).isoformat().replace("+00:00", "Z"),
        "session_id": session, "name": f"repro_{label}", "data_type": "boolean", "boolean_value": True,
        "source": "api"})
    print(f"POST /v1/scores {label:<16} -> {resp.status_code}")

time.sleep(2)
names = {s["name"] for s in requests.get(f"{BASE}/v1/sessions/{session}", headers=H).json()["scores"]}
stored = requests.post(f"{BASE}/v1/query/sql", headers=H, json={
    "sql": f"SELECT name FROM scores WHERE score_id LIKE 'repro-{run}-%'"}).json()["rows"]
print(f"\nsession {session} last span ends {end.isoformat()}")
print(f"stored in scores table: {sorted(r[0] for r in stored)}")
for label in offsets:
    print(f"  repro_{label:<16} visible in GET /v1/sessions/{{id}}: {('repro_' + label) in names}")
