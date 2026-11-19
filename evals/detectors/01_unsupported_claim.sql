-- unsupported_claim: the agent told the customer a refund was processed/issued,
-- but the session has no successful issue_refund tool call behind it.
-- (Softprobe's "hallucinated confidence": the words say done, the evidence says otherwise.)
WITH replies AS (
  SELECT session_id, trace_id, span_id, json_extract_string(http_response_body, '$.reply') AS reply
  FROM traces
  WHERE observation_type = 'agent'
),
refunds_ok AS (
  SELECT DISTINCT session_id
  FROM traces
  WHERE observation_type = 'tool'
    AND attributes['gen_ai.tool.name']::VARCHAR = 'issue_refund'
    AND status_code = 'OK'
)
SELECT r.session_id, r.trace_id, r.span_id, r.reply AS evidence
FROM replies r
-- v1: refund[^.]* -> recall 0.00 (the "." in "$249.00" ended the match)
-- v2: any refund + processed/issued within 60 chars -> precision 0.28 (matched policy text
--     like "Once issued, refunds usually show up...")
-- v3: require a completed-action claim ("has been processed", "I've issued a refund")
WHERE regexp_matches(r.reply, '(?i)(refund.{0,60}?\b(has been|was|have been)\s+(processed|issued|completed)\b|\b(i''ve|i have|we''ve|we have)\s+(processed|issued)\b.{0,30}\brefund)')
  AND r.session_id NOT IN (SELECT session_id FROM refunds_ok)
ORDER BY r.session_id
