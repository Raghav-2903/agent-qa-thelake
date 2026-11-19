-- missed_escalation: the customer asked for a person or was clearly upset, and the
-- session never reached escalate_to_human. Keyword-based customer-emotion signal;
-- see the README for what it misses.
WITH user_turns AS (
  SELECT session_id, trace_id, span_id, timestamp AS turn_ts,
         json_extract_string(http_request_body, '$.message') AS msg
  FROM traces
  WHERE observation_type = 'agent'
),
upset AS (
  SELECT session_id, arg_min(trace_id, turn_ts) AS trace_id, arg_min(span_id, turn_ts) AS span_id, string_agg(msg, ' | ') AS evidence
  FROM user_turns
  WHERE regexp_matches(msg, '(?i)\b(real person|human|speak to|talk to|a person|manager|ridiculous|angry|furious|unacceptable|third time|going in circles)\b')
  GROUP BY session_id
),
escalated AS (
  SELECT DISTINCT session_id FROM traces
  WHERE observation_type = 'tool' AND attributes['gen_ai.tool.name']::VARCHAR = 'escalate_to_human'
    AND status_code = 'OK'
)
SELECT u.session_id, u.trace_id, u.span_id, u.evidence
FROM upset u
WHERE u.session_id NOT IN (SELECT session_id FROM escalated)
ORDER BY u.session_id
