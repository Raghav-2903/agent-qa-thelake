"""Run the SQL detectors against thelake, score them against seeded ground truth,
and write the findings back to thelake as evaluation scores.

  python evals/run_evals.py                    # detect + measure + write report
  python evals/run_evals.py --post-scores      # also attach a score per flagged session
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
from datetime import datetime, timezone

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = os.environ.get("THELAKE_URL", "http://localhost:8090")
HEAD = {"X-Softprobe-Assertion": os.environ.get("THELAKE_TOKEN", ""), "content-type": "application/json"}


def sql(query: str) -> list[dict]:
    r = requests.post(f"{BASE}/v1/query/sql", headers=HEAD, json={"sql": query}, timeout=120)
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return [dict(zip(body["columns"], row)) for row in body["rows"]]


def post_score(session_id, trace_id, span_id, name, comment):
    payload = {
        "score_id": f"{name}:{session_id}",  # idempotent per detector + session
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session_id, "trace_id": trace_id, "span_id": span_id,
        "name": name, "data_type": "boolean", "boolean_value": True,
        "source": "evaluator", "comment": comment[:500],
        "metadata": {"evaluator": "agent-qa-thelake/sql-detectors-v1"},
    }
    r = requests.post(f"{BASE}/v1/scores", headers=HEAD, json=payload, timeout=30)
    r.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ground-truth", default=str(ROOT / "results/ground_truth.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--post-scores", action="store_true")
    args = ap.parse_args()

    truth = {}
    if os.path.exists(args.ground_truth):
        for line in open(args.ground_truth):
            d = json.loads(line)
            truth[d["session_id"]] = set(d["faults"] or [])
    labelled = any(truth.values())

    overview = sql("""SELECT count(DISTINCT session_id) AS sessions, count(DISTINCT trace_id) AS turns,
                             count(*) AS spans, sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
                      FROM traces""")[0]

    results, detections = [], {}
    for f in sorted((ROOT / "evals/detectors").glob("*.sql")):
        name = f.stem.split("_", 1)[1]
        t0 = time.perf_counter()
        rows = sql(f.read_text())
        ms = (time.perf_counter() - t0) * 1000
        flagged = {r["session_id"] for r in rows}
        detections[name] = rows
        res = {"detector": name, "flagged_sessions": len(flagged), "query_ms": round(ms)}
        if labelled:
            actual = {s for s, faults in truth.items() if name in faults}
            tp = len(flagged & actual)
            res |= {"seeded": len(actual), "tp": tp, "fp": len(flagged - actual), "fn": len(actual - flagged),
                    "precision": tp / len(flagged) if flagged else None,
                    "recall": tp / len(actual) if actual else None,
                    "missed_sessions": sorted(actual - flagged), "false_alarms": sorted(flagged - actual)}
        results.append(res)
        if args.post_scores:
            for r in rows:
                post_score(r["session_id"], r["trace_id"], r["span_id"], name, str(r.get("evidence", "")))

    out = pathlib.Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "detections.json").write_text(json.dumps(detections, indent=2, default=str))
    (out / "eval_metrics.json").write_text(json.dumps({"overview": overview, "detectors": results}, indent=2))

    print(f"lake: {overview['sessions']} sessions, {overview['turns']} turns, {overview['spans']} spans")
    print(f"{'detector':<20}{'flagged':>8}{'seeded':>8}{'TP':>5}{'FP':>5}{'FN':>5}{'precision':>11}{'recall':>8}{'ms':>7}")
    for r in results:
        p = f"{r['precision']:.2f}" if r.get("precision") is not None else "-"
        rc = f"{r['recall']:.2f}" if r.get("recall") is not None else "-"
        print(f"{r['detector']:<20}{r['flagged_sessions']:>8}{r.get('seeded', '-'):>8}{r.get('tp', '-'):>5}"
              f"{r.get('fp', '-'):>5}{r.get('fn', '-'):>5}{p:>11}{rc:>8}{r['query_ms']:>7}")


if __name__ == "__main__":
    main()
