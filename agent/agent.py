"""Support-agent loop with OpenTelemetry GenAI instrumentation.

Span tree per customer turn (one trace per turn, grouped by sp.session.id):

  invoke_agent larkspur-support            gen_ai.operation.name=invoke_agent
    chat <model>                           gen_ai.operation.name=chat   (+ full request/response bodies)
    execute_tool lookup_order              gen_ai.operation.name=execute_tool (+ args/result bodies)
    chat <model>
    ...

Payloads follow thelake's convention: full bodies go in `http.request` /
`http.response` span events, searchable metadata goes in attributes.
"""

from __future__ import annotations

import json

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .store import SYSTEM_PROMPT, TOOLS_SCHEMA, ToolError

AGENT_NAME = "larkspur-support"
tracer = trace.get_tracer("agent-qa-thelake")


class VirtualClock:
    """Lets a fast simulated run carry realistic span timings."""

    def __init__(self, start_ns: int):
        self.ns = start_ns

    def advance_ms(self, ms: float) -> int:
        self.ns += int(ms * 1_000_000)
        return self.ns


def _tokens(obj) -> int:
    return max(1, len(json.dumps(obj)) // 4)


def _io(span, inp, out=None):
    """Short input/output preview for thelake Explorer's Input/Output panel (sp.input / sp.output).
    Full payloads still go in the http.request / http.response events."""
    span.set_attribute("sp.input", inp if isinstance(inp, str) else json.dumps(inp))
    if out is not None:
        span.set_attribute("sp.output", out if isinstance(out, str) else json.dumps(out))


def _common(span, plan, turn):
    span.set_attribute("sp.session.id", plan.session_id)
    span.set_attribute("gen_ai.conversation.id", plan.session_id)
    span.set_attribute("sp.user.id", plan.user_id)
    span.set_attribute("gen_ai.agent.name", AGENT_NAME)
    span.set_attribute("sp.agent.name", AGENT_NAME)
    span.set_attribute("sp.turn", turn)


def run_turn(model, store, plan, messages, user_text, turn, clock, rng, max_steps=8):
    """Append one user turn to `messages`, run the agent until it replies, return the reply."""
    messages.append({"role": "user", "content": user_text})
    root = tracer.start_span(f"invoke_agent {AGENT_NAME}", start_time=clock.ns,
                             kind=trace.SpanKind.SERVER)
    _common(root, plan, turn)
    root.set_attribute("gen_ai.operation.name", "invoke_agent")
    root.set_attribute("http.request.method", "POST")
    root.set_attribute("http.request.path", "/support/chat")
    root.add_event("http.request", {"http.request.body": json.dumps({"session_id": plan.session_id,
                                                                    "message": user_text})},
                   timestamp=clock.ns)
    ctx = trace.set_span_in_context(root)
    reply = None
    tool_fn = {"lookup_order": store.lookup_order, "get_refund_policy": store.get_refund_policy,
               "issue_refund": store.issue_refund, "escalate_to_human": store.escalate_to_human}

    for _ in range(max_steps):
        # ---- model call -----------------------------------------------------------------
        request = {"model": model.name, "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                   "tools": TOOLS_SCHEMA}
        chat = tracer.start_span(f"chat {model.name}", context=ctx, start_time=clock.advance_ms(rng.uniform(5, 20)),
                                 kind=trace.SpanKind.CLIENT)
        _common(chat, plan, turn)
        chat.set_attribute("gen_ai.operation.name", "chat")
        chat.set_attribute("gen_ai.provider.name", model.provider)
        chat.set_attribute("gen_ai.request.model", model.name)
        chat.set_attribute("http.request.method", "POST")
        chat.set_attribute("http.request.path", "/v1/chat/completions")
        chat.add_event("http.request", {"http.request.body": json.dumps(request)}, timestamp=clock.ns)
        out = model.complete(request["messages"], TOOLS_SCHEMA)
        response = {"choices": [{"message": {"role": "assistant", **out},
                                 "finish_reason": "tool_calls" if out["tool_calls"] else "stop"}]}
        usage = getattr(model, "last_usage", None)
        in_tok = usage.prompt_tokens if usage else _tokens(request)
        out_tok = usage.completion_tokens if usage else _tokens(out)
        chat.set_attribute("gen_ai.response.model", model.name)
        chat.set_attribute("gen_ai.usage.input_tokens", in_tok)
        chat.set_attribute("gen_ai.usage.output_tokens", out_tok)
        chat.set_attribute("gen_ai.response.finish_reasons", [response["choices"][0]["finish_reason"]])
        chat.set_attribute("http.response.status_code", 200)
        _io(chat, request["messages"][-1], {k: v for k, v in out.items() if v})
        end = clock.advance_ms(rng.uniform(350, 900) + out_tok * rng.uniform(8, 18))
        chat.add_event("http.response", {"http.response.body": json.dumps(response)}, timestamp=end)
        chat.end(end_time=end)

        assistant = {"role": "assistant", "content": out["content"]}
        if out["tool_calls"]:
            assistant["tool_calls"] = out["tool_calls"]
        messages.append(assistant)
        if not out["tool_calls"]:
            reply = out["content"]
            break

        # ---- tool calls -----------------------------------------------------------------
        for tc in out["tool_calls"]:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            tspan = tracer.start_span(f"execute_tool {name}", context=ctx,
                                      start_time=clock.advance_ms(rng.uniform(2, 8)), kind=trace.SpanKind.INTERNAL)
            _common(tspan, plan, turn)
            tspan.set_attribute("gen_ai.operation.name", "execute_tool")
            tspan.set_attribute("gen_ai.tool.name", name)
            tspan.set_attribute("gen_ai.tool.call.id", tc["id"])
            tspan.set_attribute("http.request.method", "POST")
            tspan.set_attribute("http.request.path", f"/tools/{name}")
            tspan.add_event("http.request", {"http.request.body": json.dumps(args)}, timestamp=clock.ns)
            try:
                result = tool_fn[name](**args)
                tspan.set_attribute("http.response.status_code", 200)
                tspan.set_status(Status(StatusCode.OK))
            except (ToolError, TypeError, KeyError) as e:
                result = {"error": str(e)}
                tspan.set_attribute("http.response.status_code", 422)
                tspan.set_attribute("error.type", type(e).__name__)
                tspan.set_status(Status(StatusCode.ERROR, str(e)))
            _io(tspan, args, result)
            end = clock.advance_ms(rng.uniform(40, 250))
            tspan.add_event("http.response", {"http.response.body": json.dumps(result)}, timestamp=end)
            tspan.end(end_time=end)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})

    reply = reply or "Sorry, something went wrong on my side."
    _io(root, user_text, reply)
    root.set_attribute("http.response.status_code", 200)
    root.add_event("http.response", {"http.response.body": json.dumps({"reply": reply})}, timestamp=clock.ns)
    root.end(end_time=clock.advance_ms(rng.uniform(3, 10)))
    return reply
