"""Fake order backend for a fictional outdoor-gear store ("Larkspur Outfitters").

The tools are the agent's only way to read or change state, so every claim the
agent makes about an order or a refund should be backed by a tool span.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

REFUND_WINDOW_DAYS = 30
ORDER_ID_RE = re.compile(r"^LK-\d{5}$")


class ToolError(Exception):
    """Raised when a tool call is invalid; recorded as an ERROR span."""


@dataclass
class Order:
    order_id: str
    item: str
    total: float
    status: str  # processing | shipped | delivered
    delivered_on: date | None = None
    eta: date | None = None
    tracking: str | None = None
    refunded: float = 0.0


@dataclass
class Store:
    today: date
    orders: dict[str, Order] = field(default_factory=dict)
    escalations: list[dict] = field(default_factory=list)
    used_orders: set[str] = field(default_factory=set)

    # ---- tools exposed to the agent -------------------------------------------------

    def lookup_order(self, order_id: str) -> dict:
        if not ORDER_ID_RE.match(order_id or ""):
            raise ToolError(f"invalid order_id format: {order_id!r} (expected LK-NNNNN)")
        order = self.orders.get(order_id)
        if order is None:
            raise ToolError(f"order {order_id} not found")
        out = {
            "order_id": order.order_id,
            "item": order.item,
            "total": order.total,
            "status": order.status,
            "refunded": order.refunded,
        }
        if order.delivered_on:
            out["delivered_on"] = order.delivered_on.isoformat()
            out["days_since_delivery"] = (self.today - order.delivered_on).days
        if order.eta:
            out["eta"] = order.eta.isoformat()
        if order.tracking:
            out["tracking"] = order.tracking
        return out

    def get_refund_policy(self) -> dict:
        return {
            "window_days": REFUND_WINDOW_DAYS,
            "conditions": "Refunds within 30 days of delivery for damaged, defective or unworn items. "
            "Outside the window, offer store credit via a human agent.",
        }

    def issue_refund(self, order_id: str, amount: float, reason: str) -> dict:
        order = self.orders.get(order_id)
        if order is None:
            raise ToolError(f"order {order_id} not found")
        if amount <= 0 or amount > order.total - order.refunded:
            raise ToolError(
                f"amount {amount:.2f} exceeds refundable balance {order.total - order.refunded:.2f}"
            )
        # The backend does NOT enforce the 30-day window: that's the agent's job,
        # which is exactly why it needs to be evaluated.
        order.refunded += amount
        return {"refund_id": f"RF-{order_id[3:]}-{int(amount * 100)}", "order_id": order_id,
                "amount": amount, "status": "issued"}

    def escalate_to_human(self, reason: str, priority: str = "normal") -> dict:
        ticket = f"HD-{len(self.escalations) + 1001}"
        self.escalations.append({"ticket": ticket, "reason": reason, "priority": priority})
        return {"ticket_id": ticket, "queue": "tier2-support", "priority": priority,
                "expected_wait_minutes": 4 if priority == "high" else 12}


TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "lookup_order", "description": "Look up an order by id (format LK-NNNNN).",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                       "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "get_refund_policy", "description": "Return the store refund policy.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "issue_refund", "description": "Issue a refund. Only allowed within the refund window.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}},
            "required": ["order_id", "amount", "reason"]}}},
    {"type": "function", "function": {
        "name": "escalate_to_human",
        "description": "Hand the conversation to a human agent. Use when the customer asks for a person, "
                       "is clearly upset, or the request is outside policy.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string"}, "priority": {"type": "string", "enum": ["normal", "high"]}},
            "required": ["reason"]}}},
]

SYSTEM_PROMPT = (
    "You are the customer-support agent for Larkspur Outfitters. Use tools for every fact about an "
    "order or refund; never claim an action happened unless a tool confirmed it. Refunds are only "
    "allowed within 30 days of delivery. If the customer asks for a human or is clearly upset, call "
    "escalate_to_human. Remember details the customer already gave you."
)


def seed_store(rng, today: date, n_orders: int = 400) -> Store:
    items = [("Trail runner shoes", 129.0), ("2-person tent", 249.0), ("Down jacket", 189.0),
             ("Hydration pack", 64.5), ("Trekking poles", 89.0), ("Sleeping bag", 159.0),
             ("Rain shell", 139.0), ("Camp stove", 74.0)]
    store = Store(today=today)
    for i in range(n_orders):
        oid = f"LK-{40000 + i:05d}"
        item, price = rng.choice(items)
        status = rng.choices(["processing", "shipped", "delivered"], [1, 2, 5])[0]
        order = Order(order_id=oid, item=item, total=price, status=status)
        if status == "delivered":
            order.delivered_on = today - timedelta(days=rng.randint(2, 60))
        else:
            order.eta = today + timedelta(days=rng.randint(1, 7))
            if status == "shipped":
                order.tracking = f"1Z{rng.randint(10**9, 10**10 - 1)}"
        store.orders[oid] = order
    return store
