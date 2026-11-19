"""Generate support-agent sessions and export their traces to thelake over OTLP/HTTP.

  python run_traffic.py --sessions 120 --seed 7                 # scripted model, seeded faults
  python run_traffic.py --sessions 20 --model gpt-4o-mini       # real model (OPENAI_API_KEY / Azure env)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import date

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from agent.agent import AGENT_NAME, VirtualClock, run_turn
from agent.models import OpenAIModel, ScriptedModel
from agent.scenarios import first_message, labels, make_plan, next_user_message
from agent.store import seed_store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fault-rate", type=float, default=0.35, help="share of sessions given one seeded fault")
    ap.add_argument("--model", default="scripted", help="'scripted' or a chat model / Azure deployment name")
    ap.add_argument("--endpoint", default=os.environ.get("THELAKE_OTLP_ENDPOINT", "http://localhost:8090/v1/traces"))
    ap.add_argument("--ground-truth", default="results/ground_truth.jsonl")
    args = ap.parse_args()

    token = os.environ["THELAKE_TOKEN"]
    provider = TracerProvider(resource=Resource.create({"service.name": AGENT_NAME, "service.version": "0.1.0",
                                                        "deployment.environment": "sim"}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=args.endpoint, headers={"X-Softprobe-Assertion": token}),
        max_export_batch_size=256))
    trace.set_tracer_provider(provider)

    rng = random.Random(args.seed)
    store = seed_store(rng, today=date.today())
    scripted = args.model == "scripted"
    start = time.time_ns() - 8 * 3600 * 10**9  # spread sessions over the last 8 hours
    os.makedirs(os.path.dirname(args.ground_truth), exist_ok=True)

    with open(args.ground_truth, "w") as gt:
        for i in range(args.sessions):
            plan = make_plan(rng, store, i, args.fault_rate if scripted else 0.0)
            model = ScriptedModel(plan) if scripted else OpenAIModel(args.model)
            clock = VirtualClock(start + int(i * (8 * 3600 / args.sessions) * 10**9))
            messages, text, turn, transcript = [], first_message(plan), 1, []
            while text:
                reply = run_turn(model, store, plan, messages, text, turn, clock, rng)
                transcript.append({"user": text, "agent": reply})
                clock.advance_ms(rng.uniform(8_000, 45_000))  # customer types the next message
                turn += 1
                text = next_user_message(plan, turn - 1, reply)
            gt.write(json.dumps({"session_id": plan.session_id, "scenario": plan.scenario,
                                 "faults": labels(plan) if scripted else None, "model": model.name,
                                 "turns": len(transcript), "transcript": transcript}) + "\n")
    provider.force_flush()
    provider.shutdown()
    print(f"exported {args.sessions} sessions -> {args.endpoint}; labels -> {args.ground_truth}")


if __name__ == "__main__":
    main()
