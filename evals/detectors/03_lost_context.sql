-- lost_context: the model asked the customer for their order number even though a
-- user message already in its own prompt contained one. Checked per LLM call, using
-- the full request/response payloads thelake keeps.
WITH gen AS (
  SELECT session_id, trace_id, span_id, http_request_body AS req,
         json_extract_string(http_response_body, '$.choices[0].message.content') AS answer
  FROM traces
  WHERE observation_type = 'generation'
)
SELECT session_id, trace_id, span_id, answer AS evidence
FROM gen
WHERE regexp_matches(answer, '(?i)(share|provide|send|give|confirm)[^.?]*order (number|id)')
  AND (SELECT count(*) FROM unnest(json_extract(req, '$.messages[*]')) AS m(x)
       WHERE x->>'role' = 'user' AND regexp_matches(x->>'content', 'LK-\d{5}')) > 0
ORDER BY session_id
