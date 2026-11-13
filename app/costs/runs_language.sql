UPDATE provider_calls p SET language = m.lang, attribution = 'run'
FROM (SELECT DISTINCT ON (p2.id) p2.id AS id, a.language AS lang
      FROM provider_calls p2 JOIN agent_runs a
        ON a.case_id = p2.case_id AND a.input_tokens = p2.tokens_in AND a.output_tokens = p2.tokens_out AND a.language IS NOT NULL
       AND abs(extract(epoch FROM (coalesce(a.completed_at, a.started_at) - p2.created_at))) < 5
      WHERE p2.host = 'budget-ledger' AND p2.language IS NULL
      ORDER BY p2.id, abs(extract(epoch FROM (coalesce(a.completed_at, a.started_at) - p2.created_at)))) m
WHERE p.id = m.id
