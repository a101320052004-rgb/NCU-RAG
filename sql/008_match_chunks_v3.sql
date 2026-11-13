-- 2026-11-13 已套用到 Supabase（migration: match_chunks_v3_candidates_first）
-- 新增 match_chunks_v3（不改 v2）：先用便宜條件取向量最近的候選，再只對候選做法規檢查。
-- 原因：v2 每次都對全部 1,398 段跑 chunk_is_eligible（法規正則比對），一次約 1 秒；
-- anon 角色 statement_timeout=3s，4 個問題同時進來就全部逾時（HTTP 500, code 57014）。
-- v3 一次約 45 ms。被法規擋下的段落目前 24 段；候選多取 100 段，擋下的段落少於 100 時結果和 v2 完全相同
-- （已用 6 個查詢向量 × 有無主題篩選比對，12 組全部相同）。
-- 之後被擋下的段落若超過 100 段，要把 +100 調大。
CREATE OR REPLACE FUNCTION public.match_chunks_v3(
  query_embedding extensions.vector,
  match_count integer DEFAULT 15,
  p_identity text DEFAULT NULL,
  p_topic text DEFAULT NULL,
  p_stale_before date DEFAULT ((CURRENT_DATE - '1 year'::interval))::date)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO ''
AS $function$
  WITH cand AS (
    SELECT c, (c.embedding OPERATOR(extensions.<=>) query_embedding)::float8 AS dist
    FROM public.chunks c
    WHERE c.status = 'active'
      AND c.last_verified >= p_stale_before
      AND (c.valid_until IS NULL OR c.valid_until >= (now() AT TIME ZONE 'Asia/Taipei')::date)
      AND (p_identity IS NULL OR p_identity = ANY(c.identities) OR '全體境外生' = ANY(c.identities))
      AND (p_topic IS NULL OR p_topic = ANY(c.topics))
    ORDER BY c.embedding OPERATOR(extensions.<=>) query_embedding
    LIMIT least(greatest(match_count, 1), 50) + 100
  )
  SELECT public.retrieval_chunk_payload(cand.c) || jsonb_build_object('distance', cand.dist)
  FROM cand
  WHERE public.chunk_is_eligible(cand.c, p_identity, p_topic, p_stale_before)
  ORDER BY cand.dist
  LIMIT least(greatest(match_count, 1), 50);
$function$;

GRANT EXECUTE ON FUNCTION public.match_chunks_v3(extensions.vector, integer, text, text, date) TO anon, authenticated, service_role;
