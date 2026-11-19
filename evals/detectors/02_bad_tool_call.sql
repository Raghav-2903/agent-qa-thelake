-- bad_tool_call: tool spans that failed validation (bad arguments, wrong ids, amounts
-- over the refundable balance), plus whether the agent recovered with a later
-- successful call to the same tool in the same turn.
WITH tool_spans AS (
  SELECT session_id, trace_id, span_id, timestamp,
         attributes['gen_ai.tool.name']::VARCHAR AS tool,
         status_code, status_message, http_request_body AS args
  FROM traces
  WHERE observation_type = 'tool'
)
SELECT e.session_id, e.trace_id, e.span_id,
       e.tool || '(' || e.args || ') -> ' || e.status_message AS evidence,
       EXISTS (SELECT 1 FROM tool_spans s
               WHERE s.trace_id = e.trace_id AND s.tool = e.tool
                 AND s.status_code = 'OK' AND s.timestamp > e.timestamp) AS recovered
FROM tool_spans e
WHERE e.status_code = 'ERROR'
ORDER BY e.session_id
