-- policy_violation: a refund was issued for an order the agent had itself looked up
-- as delivered more than 30 days ago (the backend doesn't enforce the window; the
-- agent is supposed to).
WITH lookups AS (
  SELECT session_id, timestamp,
         json_extract_string(http_response_body, '$.order_id') AS order_id,
         TRY_CAST(json_extract_string(http_response_body, '$.days_since_delivery') AS INTEGER) AS days
  FROM traces
  WHERE observation_type = 'tool' AND attributes['gen_ai.tool.name']::VARCHAR = 'lookup_order'
    AND status_code = 'OK'
),
refunds AS (
  SELECT session_id, trace_id, span_id, timestamp, http_request_body AS args,
         json_extract_string(http_request_body, '$.order_id') AS order_id
  FROM traces
  WHERE observation_type = 'tool' AND attributes['gen_ai.tool.name']::VARCHAR = 'issue_refund'
    AND status_code = 'OK'
)
SELECT DISTINCT r.session_id, r.trace_id, r.span_id,
       'issue_refund(' || r.args || ') after lookup showed ' || l.days || ' days since delivery' AS evidence
FROM refunds r
JOIN lookups l ON l.session_id = r.session_id AND l.order_id = r.order_id AND l.timestamp < r.timestamp
WHERE l.days > 30
ORDER BY r.session_id
