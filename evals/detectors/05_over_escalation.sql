-- over_escalation: the agent handed off to a human although the customer showed no
-- sign of wanting one and the agent had not offered a handoff in its previous reply
-- (e.g. for an out-of-policy request). Wastes tier-2 capacity on routine questions.
WITH turns AS (
  SELECT session_id, trace_id, attributes['sp.turn']::INTEGER AS turn,
         json_extract_string(http_request_body, '$.message') AS msg,
         json_extract_string(http_response_body, '$.reply') AS reply
  FROM traces
  WHERE observation_type = 'agent'
),
escalations AS (
  SELECT session_id, trace_id, span_id, http_request_body AS args
  FROM traces
  WHERE observation_type = 'tool' AND attributes['gen_ai.tool.name']::VARCHAR = 'escalate_to_human'
)
SELECT e.session_id, e.trace_id, e.span_id, t.msg || '  =>  escalate_to_human(' || e.args || ')' AS evidence
FROM escalations e
JOIN turns t ON t.trace_id = e.trace_id
LEFT JOIN turns prev ON prev.session_id = t.session_id AND prev.turn = t.turn - 1
WHERE NOT regexp_matches(t.msg, '(?i)\b(real person|human|speak to|talk to|a person|manager|ridiculous|angry|furious|unacceptable|third time|going in circles)\b')
  AND NOT coalesce(regexp_matches(prev.reply, '(?i)(connect you|teammate|would you like)'), false)
ORDER BY e.session_id
