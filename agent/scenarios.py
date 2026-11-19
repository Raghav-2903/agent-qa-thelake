"""Customer scenarios, seeded faults and a small user simulator.

A scenario fixes who the customer is and what they want. A *fault* is a known
agent mistake the scripted model will make in that session. Faults are written
to ground_truth.jsonl only; they never appear in the telemetry, so the SQL
detectors have to find them from the traces alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# fault -> scenarios where it can occur
FAULTS = {
    "bad_tool_call": ["order_status", "refund_eligible"],
    "unsupported_claim": ["refund_eligible"],
    "lost_context": ["order_status", "refund_eligible"],
    "missed_escalation": ["frustrated"],
    "over_escalation": ["faq"],
    "policy_violation": ["refund_late"],
}

SCENARIO_WEIGHTS = {"order_status": 3, "refund_eligible": 3, "refund_late": 2, "frustrated": 2, "faq": 2}

FRUSTRATED_OPENERS = [
    # explicit: asks for a person
    "This is the third time I'm writing about order {oid}. Nobody has helped. I want to speak to a real person.",
    "Can I please talk to a human? Order {oid} is a mess and the bot keeps going in circles.",
    # explicit: strong emotion
    "Honestly this is ridiculous. My {item} (order {oid}) is still not here and my trip is this weekend.",
    "I'm really angry right now. Order {oid} was supposed to arrive days ago.",
    # implicit: no obvious keywords (a keyword detector should miss some of these)
    "I've been waiting two weeks for order {oid} and I'm running out of patience.",
    "Order {oid}. Again. I've explained this twice already, I'm done explaining.",
]

FAQ_QUESTIONS = [
    "What's your return window?",
    "Can I return shoes I've already worn on a trail?",
    "How long do refunds take to show up?",
]


@dataclass
class Plan:
    session_id: str
    user_id: str
    scenario: str
    fault: str | None
    order_id: str | None
    item: str | None
    variant: int  # picks phrasing / sub-behaviour deterministically


def pick_order(rng, store, scenario):
    # each order is used by at most one conversation, so refunds never collide
    orders = [o for o in store.orders.values() if o.order_id not in store.used_orders]
    if scenario == "order_status":
        pool = [o for o in orders if o.status in ("processing", "shipped")]
    elif scenario == "refund_eligible":
        pool = [o for o in orders if o.status == "delivered" and (store.today - o.delivered_on).days <= 28]
    elif scenario == "refund_late":
        pool = [o for o in orders if o.status == "delivered" and (store.today - o.delivered_on).days >= 35]
    elif scenario == "frustrated":
        pool = [o for o in orders if o.status in ("processing", "shipped")]
    else:
        return None
    order = rng.choice(pool)
    store.used_orders.add(order.order_id)
    return order


def labels(plan) -> list[str]:
    """All mistakes the scripted model will make in this session (ground truth)."""
    if plan.fault is None:
        return []
    out = [plan.fault]
    if plan.fault == "unsupported_claim" and plan.variant % 2 == 1:
        out.append("bad_tool_call")  # this variant first sends a bad refund amount, then claims success anyway
    return out


def make_plan(rng, store, idx: int, fault_rate: float) -> Plan:
    scenario = rng.choices(list(SCENARIO_WEIGHTS), list(SCENARIO_WEIGHTS.values()))[0]
    applicable = [f for f, scs in FAULTS.items() if scenario in scs]
    fault = rng.choice(applicable) if applicable and rng.random() < fault_rate else None
    order = pick_order(rng, store, scenario)
    return Plan(
        session_id=f"sess-{idx:04d}",
        user_id=f"cust-{rng.randint(1000, 9999)}",
        scenario=scenario,
        fault=fault,
        order_id=order.order_id if order else None,
        item=order.item if order else None,
        variant=rng.randint(0, 999),
    )


def first_message(plan: Plan) -> str:
    oid, item, v = plan.order_id, plan.item, plan.variant
    if plan.scenario == "order_status":
        return ["Hi, where is my order {oid}?", "Any update on order {oid}? Placed it last week.",
                "Hey, can you check the status of {oid} for me?"][v % 3].format(oid=oid)
    if plan.scenario in ("refund_eligible", "refund_late"):
        return ["My {item} from order {oid} arrived damaged. I'd like a refund.",
                "Hi, I want to return order {oid}, the {item} doesn't fit at all. Refund please.",
                "The {item} I got (order {oid}) is defective. Can I get my money back?"][v % 3].format(
                    oid=oid, item=item.lower())
    if plan.scenario == "frustrated":
        return FRUSTRATED_OPENERS[v % len(FRUSTRATED_OPENERS)].format(oid=oid, item=item.lower())
    return FAQ_QUESTIONS[v % len(FAQ_QUESTIONS)]


ASKS_FOR_ORDER_ID = re.compile(r"(share|provide|send|give)[^.?]*order (number|id)", re.I)


def next_user_message(plan: Plan, turn: int, last_reply: str) -> str | None:
    """Very small user simulator: reacts to what the agent just said."""
    if turn >= 3:
        return None
    if ASKS_FOR_ORDER_ID.search(last_reply) and plan.order_id:
        return f"I already gave it to you: {plan.order_id}."
    s = plan.scenario
    if s == "order_status":
        return ["Thanks. So when exactly will it arrive?", "Great, what day should I expect it?"][plan.variant % 2] \
            if turn == 1 else ("Perfect, thanks!" if turn == 2 else None)
    if s == "refund_eligible":
        return "How long until I see the money back?" if turn == 1 else None
    if s == "refund_late":
        if "teammate" in last_reply or "store credit" in last_reply:
            return "Yes please, store credit is fine." if turn == 1 else None
        return "Ok, thanks." if turn == 1 else None
    if s == "frustrated":
        if "ticket" in last_reply.lower():
            return "Fine. Thank you." if turn == 1 else None
        return "That's not what I asked. Get me a person." if turn == 1 else None
    if s == "faq":
        return "Got it, thanks." if turn == 1 else None
    return None
