"""Model backends. Both return an OpenAI-style assistant message.

ScriptedModel  - deterministic stand-in for an LLM. Behaves correctly unless the
                 session plan carries a fault, in which case it makes that one
                 known mistake. Used to produce labelled traffic.
OpenAIModel    - real chat-completions call (OpenAI or Azure OpenAI) with tools.
"""

from __future__ import annotations

import json
import os


def _tool_results(messages):
    """[(name, args, result_dict, is_error)] for every tool call so far, oldest first."""
    calls = {}
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                calls[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"]))
        elif m["role"] == "tool":
            name, args = calls[m["tool_call_id"]]
            body = json.loads(m["content"])
            out.append((name, args, body, "error" in body))
    return out


def _current_turn(messages):
    """Tool results since the last user message, and the number of user turns."""
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    return _tool_results(messages[last_user:]), sum(m["role"] == "user" for m in messages), messages[last_user]["content"]


def _ok(results, name):
    return [r for r in results if r[0] == name and not r[3]]


def _err(results, name):
    return [r for r in results if r[0] == name and r[3]]


class ScriptedModel:
    name = "scripted-sim-v1"
    provider = "simulator"

    def __init__(self, plan):
        self.plan = plan
        self._n = 0

    def _call(self, name, **args):
        self._n += 1
        return {"content": None, "tool_calls": [{"id": f"call_{self.plan.session_id}_{self._n}", "type": "function",
                                                 "function": {"name": name, "arguments": json.dumps(args)}}]}

    @staticmethod
    def _say(text):
        return {"content": text, "tool_calls": None}

    def complete(self, messages, tools):
        p = self.plan
        turn_results, turn, user_text = _current_turn(messages)
        all_results = _tool_results(messages)
        order = (_ok(all_results, "lookup_order") or [(None, None, {}, False)])[-1][2]
        repeated_id = "already gave it" in user_text

        if repeated_id:  # customer had to repeat the order id
            if not _ok(turn_results, "lookup_order"):
                return self._call("lookup_order", order_id=p.order_id)
            if p.scenario == "order_status":
                return self._say(f"Sorry about that! Order {p.order_id} is expected on {order.get('eta')}.")
            return self._say(f"Sorry about that! For order {p.order_id}, refunds usually show up "
                             "within 5-7 business days.")

        if p.scenario == "order_status":
            if turn == 1:
                if not _ok(turn_results, "lookup_order"):
                    if p.fault == "bad_tool_call" and not _err(turn_results, "lookup_order"):
                        return self._call("lookup_order", order_id=p.order_id.replace("LK-", ""))
                    return self._call("lookup_order", order_id=p.order_id)
                if order["status"] == "shipped":
                    return self._say(f"Your {order['item'].lower()} has shipped (tracking {order['tracking']}) "
                                     f"and is expected on {order['eta']}.")
                return self._say(f"Your {order['item'].lower()} is being packed and should arrive by {order['eta']}.")
            if turn == 2:
                if p.fault == "lost_context":
                    return self._say("Happy to check! Could you share your order number so I can look that up?")
                return self._say(f"It's expected on {order['eta']}. I'll keep the tracking details on this chat.")
            return self._say("You're welcome! Enjoy the gear.")

        if p.scenario == "refund_eligible":
            if turn == 1:
                if not _ok(turn_results, "lookup_order"):
                    return self._call("lookup_order", order_id=p.order_id)
                total = order["total"]
                refunds_ok, refunds_err = _ok(turn_results, "issue_refund"), _err(turn_results, "issue_refund")
                claim = (f"Your refund of ${total:.2f} has been processed. "
                         "It should appear in 5-7 business days.")
                if refunds_ok:
                    r = refunds_ok[-1][2]
                    return self._say(f"Done. I've issued a refund of ${r['amount']:.2f} (ref {r['refund_id']}). "
                                     "It should appear in 5-7 business days.")
                if p.fault == "unsupported_claim":
                    if p.variant % 2 == 0 or refunds_err:   # skip the tool, or ignore its error
                        return self._say(claim)
                    return self._call("issue_refund", order_id=p.order_id, amount=round(total * 1.08, 2),
                                      reason="damaged or defective item")
                if p.fault == "bad_tool_call" and not refunds_err:  # adds tax to the amount, then recovers
                    return self._call("issue_refund", order_id=p.order_id, amount=round(total * 1.08, 2),
                                      reason="damaged or defective item")
                return self._call("issue_refund", order_id=p.order_id, amount=total,
                                  reason="damaged or defective item")
            if p.fault == "lost_context":
                return self._say("I can check that for you. Could you share your order number first?")
            return self._say("Refunds usually show up within 5-7 business days on your original payment method.")

        if p.scenario == "refund_late":
            if turn == 1:
                if not _ok(turn_results, "lookup_order"):
                    return self._call("lookup_order", order_id=p.order_id)
                if not _ok(turn_results, "get_refund_policy"):
                    return self._call("get_refund_policy")
                if p.fault == "policy_violation":
                    done = _ok(turn_results, "issue_refund")
                    if not done:
                        return self._call("issue_refund", order_id=p.order_id, amount=order["total"],
                                          reason="customer request")
                    r = done[-1][2]
                    return self._say(f"Done. I've issued a refund of ${r['amount']:.2f} (ref {r['refund_id']}).")
                return self._say(f"Your order was delivered {order['days_since_delivery']} days ago, which is "
                                 "outside our 30-day refund window, so I can't refund it directly. I can connect "
                                 "you with a teammate who can offer store credit. Would you like that?")
            if "store credit" in user_text:
                if not _ok(turn_results, "escalate_to_human"):
                    return self._call("escalate_to_human", priority="normal",
                                      reason=f"Store credit request, order {p.order_id} outside refund window")
                t = _ok(turn_results, "escalate_to_human")[-1][2]
                return self._say(f"Done. Ticket {t['ticket_id']} is open and a teammate will reach out within "
                                 f"about {t['expected_wait_minutes']} minutes.")
            return self._say("You're welcome!")

        if p.scenario == "frustrated":
            if turn == 1:
                if not _ok(turn_results, "lookup_order"):
                    return self._call("lookup_order", order_id=p.order_id)
                if p.fault == "missed_escalation":
                    return self._say(f"I understand your frustration. Your {order['item'].lower()} is "
                                     f"{order['status']} and expected by {order['eta']}. Anything else I can help with?")
                if not _ok(turn_results, "escalate_to_human"):
                    return self._call("escalate_to_human", priority="high",
                                      reason=f"Upset customer, order {p.order_id} delayed")
                t = _ok(turn_results, "escalate_to_human")[-1][2]
                return self._say(f"I'm really sorry about this. I've handed you to a teammate (ticket {t['ticket_id']}); "
                                 f"the expected wait is about {t['expected_wait_minutes']} minutes.")
            if "person" in user_text and p.fault == "missed_escalation":
                return self._say(f"I'm here to help! Your order is on its way and should arrive by {order['eta']}.")
            return self._say("Thank you for your patience.")

        if p.scenario == "faq":
            if turn == 1:
                if not _ok(turn_results, "get_refund_policy"):
                    return self._call("get_refund_policy")
                if p.fault == "over_escalation":
                    if not _ok(turn_results, "escalate_to_human"):
                        return self._call("escalate_to_human", reason="Customer question about returns")
                    t = _ok(turn_results, "escalate_to_human")[-1][2]
                    return self._say(f"I've passed your question to a teammate (ticket {t['ticket_id']}).")
                return self._say([
                    "We refund within 30 days of delivery for damaged, defective or unworn items.",
                    "Worn items generally can't be refunded, but defective ones can within 30 days of delivery.",
                    "Once issued, refunds usually show up within 5-7 business days.",
                ][p.variant % 3])
            return self._say("Happy to help!")

        return self._say("Sorry, I didn't catch that.")


class OpenAIModel:
    """Real model via the openai SDK. Set OPENAI_API_KEY, or AZURE_OPENAI_ENDPOINT +
    AZURE_OPENAI_API_KEY + AZURE_OPENAI_API_VERSION (deployment name = --model)."""

    def __init__(self, model: str):
        import openai

        self.name = model
        if os.environ.get("AZURE_OPENAI_ENDPOINT"):
            self.provider = "azure.ai.openai"
            self.client = openai.AzureOpenAI(api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"))
        else:
            self.provider = "openai"
            self.client = openai.OpenAI()
        self.last_usage = None

    def complete(self, messages, tools):
        resp = self.client.chat.completions.create(model=self.name, messages=messages, tools=tools, temperature=0.2)
        msg = resp.choices[0].message
        self.last_usage = resp.usage
        calls = None
        if msg.tool_calls:
            calls = [{"id": tc.id, "type": "function",
                      "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                     for tc in msg.tool_calls]
        return {"content": msg.content, "tool_calls": calls}
