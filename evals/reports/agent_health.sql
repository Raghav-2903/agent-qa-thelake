-- One row per agent-health metric family, straight from the evidence lake.
-- Turn latency = invoke_agent span duration; cost proxy = tokens.
WITH turns AS (
  SELECT session_id, trace_id,
         date_diff('millisecond', timestamp, end_timestamp) AS turn_ms
  FROM traces WHERE observation_type = 'agent'
),
tools AS (
  SELECT attributes['gen_ai.tool.name']::VARCHAR AS tool, status_code
  FROM traces WHERE observation_type = 'tool'
),
gens AS (
  SELECT session_id, input_tokens, output_tokens FROM traces WHERE observation_type = 'generation'
)
SELECT 'turn latency p50 / p95 (ms)' AS metric,
       round(quantile_cont(turn_ms, 0.5))::VARCHAR || ' / ' || round(quantile_cont(turn_ms, 0.95))::VARCHAR AS value
FROM turns
UNION ALL
SELECT 'LLM calls per turn',
       round((SELECT count(*) FROM gens) / (SELECT count(*) FROM turns), 2)::VARCHAR
UNION ALL
SELECT 'tokens per session (avg in / out)',
       round(avg(i))::VARCHAR || ' / ' || round(avg(o))::VARCHAR
FROM (SELECT session_id, sum(input_tokens) i, sum(output_tokens) o FROM gens GROUP BY 1)
UNION ALL
SELECT 'tool error rate: ' || tool,
       (count(*) FILTER (WHERE status_code = 'ERROR'))::VARCHAR || ' / ' || count(*)::VARCHAR
FROM tools GROUP BY tool
ORDER BY metric
