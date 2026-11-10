-- PREPARED ONLY. Apply to an isolated PostgreSQL database before production review.
-- Original RPC signatures remain unchanged. Application v2 requires this migration.
BEGIN;

-- Preserve exact prior rows for all existing ingestion/admin writers, including
-- same-ID replacement. No public read policy; snapshots contain official KB only.
CREATE TABLE IF NOT EXISTS private.chunk_version_history (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chunk_id text NOT NULL,
  operation text NOT NULL,
  archived_at timestamptz NOT NULL DEFAULT now(),
  snapshot jsonb NOT NULL
);
ALTER TABLE private.chunk_version_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON private.chunk_version_history FROM PUBLIC;
CREATE OR REPLACE FUNCTION private.archive_chunk_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO '' AS $fn$
BEGIN
 IF TG_OP='DELETE' OR OLD IS DISTINCT FROM NEW THEN
  INSERT INTO private.chunk_version_history(chunk_id,operation,snapshot)
  VALUES(OLD.id,TG_OP,to_jsonb(OLD));
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $fn$;
DROP TRIGGER IF EXISTS reliability_archive_chunk ON public.chunks;
CREATE TRIGGER reliability_archive_chunk BEFORE UPDATE OR DELETE ON public.chunks
FOR EACH ROW EXECUTE FUNCTION private.archive_chunk_version();

CREATE TABLE IF NOT EXISTS public.chunk_policy_reviews (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chunk_id text NOT NULL,
  body_sha256 text NOT NULL CHECK (length(body_sha256) = 64),
  review_status text NOT NULL CHECK (review_status IN ('approved','rejected','needs_review')),
  reviewed_at date NOT NULL,
  note text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunk_policy_reviews_lookup ON public.chunk_policy_reviews(chunk_id, body_sha256, id DESC);
ALTER TABLE public.chunk_policy_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chunk_policy_reviews FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.reliability_version() RETURNS integer
LANGUAGE sql IMMUTABLE SET search_path TO '' AS $fn$ SELECT 2; $fn$;

CREATE OR REPLACE FUNCTION public.policy_claim_rule(p_body text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path TO '' AS $fn$
DECLARE t text := lower(regexp_replace(coalesce(p_body,''), '\s+', ' ', 'g'));
        arc boolean; current_arc boolean := false; current_work boolean := false;
BEGIN
 arc := t ~ '(\yarc\y|alien resident certificate|外僑居留證|居留證)';
 IF arc AND (t ~ '(arrival|arriving|arrive|receiving (a )?resident visa|入[境國]).{0,130}(?<![0-9])(15|十五)\s*(days?\y|日|天)'
          OR t ~ '(?<![0-9])(15|十五)\s*(days?\y|日|天).{0,100}(arrival|arriving|arrive|receiving (a )?resident visa|入[境國])') THEN
   RETURN 'obsolete:arc_arrival_deadline';
 END IF;
 current_arc := arc AND (t ~ '(arrival|arriving|arrive|receiving (a )?resident visa|入[境國]).{0,130}(?<![0-9])(30|三十)\s*(days?\y|日|天)'
                    OR t ~ '(?<![0-9])(30|三十)\s*(days?\y|日|天).{0,100}(arrival|arriving|arrive|receiving (a )?resident visa|入[境國])');
 IF t ~ '(work permit|工作許可|工作證)' THEN
   IF t ~ '(最長|許可期間|有效期間|max(imum)?|valid|duration).{0,65}(?<![0-9])(6\s*months?\y|6\s*個月|六個月)' THEN
     RETURN 'obsolete:student_work_permit_duration';
   END IF;
   current_work := t ~ '(最長|許可期間|有效期間|max(imum)?|valid|duration).{0,65}(?<![0-9])(1\s*year\y|one year\y|一年)';
 END IF;
 IF current_arc OR current_work THEN RETURN 'review:regulated_claim'; END IF;
 RETURN NULL;
END $fn$;

CREATE OR REPLACE FUNCTION public.policy_review_metadata(p_id text, p_body text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO '' AS $fn$
 SELECT coalesce((SELECT jsonb_build_object(
   'policy_review_status',r.review_status,'policy_reviewed_at',r.reviewed_at,
   'policy_reviewed_sha256',r.body_sha256)
 FROM public.chunk_policy_reviews r
 WHERE r.chunk_id=p_id AND r.body_sha256=encode(sha256(convert_to(p_body,'UTF8')),'hex')
 ORDER BY r.id DESC LIMIT 1),'{}'::jsonb);
$fn$;

CREATE OR REPLACE FUNCTION public.chunk_is_eligible(c public.chunks, p_identity text, p_topic text, p_stale_before date)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO '' AS $fn$
 SELECT c.status='active'
 AND c.last_verified >= p_stale_before
 AND (c.valid_until IS NULL OR c.valid_until >= (now() AT TIME ZONE 'Asia/Taipei')::date)
 AND (p_identity IS NULL OR p_identity=ANY(c.identities) OR '全體境外生'=ANY(c.identities))
 AND (p_topic IS NULL OR p_topic=ANY(c.topics))
 AND CASE
  WHEN public.policy_claim_rule(c.body) LIKE 'obsolete:%' THEN false
  WHEN public.policy_claim_rule(c.body) LIKE 'review:%' THEN
   coalesce(public.policy_review_metadata(c.id,c.body)->>'policy_review_status'='approved'
    AND (public.policy_review_metadata(c.id,c.body)->>'policy_reviewed_at')::date BETWEEN p_stale_before AND (now() AT TIME ZONE 'Asia/Taipei')::date,false)
  ELSE true END;
$fn$;

CREATE OR REPLACE FUNCTION public.retrieval_chunk_payload(c public.chunks) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO '' AS $fn$
 SELECT jsonb_build_object('id',c.id,'title',c.title,'url',c.url,'lang',c.lang,'body',c.body,
   'topics',c.topics,'identities',c.identities,'status',c.status,
   'valid_until',c.valid_until,'last_verified',c.last_verified)
   || public.policy_review_metadata(c.id,c.body);
$fn$;

CREATE OR REPLACE FUNCTION public.match_chunks_v2(query_embedding extensions.vector, match_count integer DEFAULT 15,
 p_identity text DEFAULT NULL,p_topic text DEFAULT NULL,p_stale_before date DEFAULT (CURRENT_DATE - interval '1 year')::date)
RETURNS SETOF jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO '' AS $fn$
 SELECT public.retrieval_chunk_payload(c) || jsonb_build_object('distance',(c.embedding OPERATOR(extensions.<=>) query_embedding)::float8)
 FROM public.chunks c WHERE public.chunk_is_eligible(c,p_identity,p_topic,p_stale_before)
 ORDER BY c.embedding OPERATOR(extensions.<=>) query_embedding LIMIT least(greatest(match_count,1),50);
$fn$;

CREATE OR REPLACE FUNCTION public.get_chunks_v2(p_ids text[],p_identity text DEFAULT NULL,p_topic text DEFAULT NULL,
 p_stale_before date DEFAULT (CURRENT_DATE - interval '1 year')::date)
RETURNS SETOF jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO '' AS $fn$
 SELECT public.retrieval_chunk_payload(c) FROM public.chunks c
 WHERE c.id=ANY(p_ids[1:30]) AND public.chunk_is_eligible(c,p_identity,p_topic,p_stale_before);
$fn$;

CREATE OR REPLACE FUNCTION public.find_partner_chunks_v2(p_terms text[],p_identity text DEFAULT NULL,p_topic text DEFAULT NULL,
 p_stale_before date DEFAULT (CURRENT_DATE - interval '1 year')::date)
RETURNS SETOF jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO '' AS $fn$
 SELECT public.retrieval_chunk_payload(c) FROM public.chunks c
 WHERE c.id LIKE 'sheet-partners-%'
 AND public.chunk_is_eligible(c,p_identity,p_topic,p_stale_before)
 AND EXISTS (SELECT 1 FROM unnest(p_terms[1:12]) t WHERE length(t)>=4 AND c.body ILIKE '%'||t||'%')
 LIMIT 3;
$fn$;

-- Explicit approvals only: a fetched_at/last_verified timestamp is never copied into an approval.
CREATE OR REPLACE FUNCTION public.ingest_upsert_v2(p_secret text,p_run_id text,p_rows jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO '' AS $fn$
DECLARE n integer; r jsonb;
BEGIN
 IF p_secret IS NULL OR p_secret='' OR p_secret IS DISTINCT FROM (SELECT value FROM private.settings WHERE key='ingest_secret') THEN
  RAISE EXCEPTION 'invalid ingest secret';
 END IF;
 n := public.ingest_upsert(p_secret,p_run_id,p_rows);
 FOR r IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
  IF r->>'policy_review_status'='approved' THEN
   IF r->>'policy_reviewed_sha256' IS DISTINCT FROM encode(sha256(convert_to(r->>'body','UTF8')),'hex')
     OR nullif(r->>'policy_reviewed_at','') IS NULL
     OR (r->>'policy_reviewed_at')::date > (now() AT TIME ZONE 'Asia/Taipei')::date
     OR public.policy_claim_rule(r->>'body') LIKE 'obsolete:%' THEN
    RAISE EXCEPTION 'invalid explicit policy approval';
   END IF;
   INSERT INTO public.chunk_policy_reviews(chunk_id,body_sha256,review_status,reviewed_at,note)
   VALUES(r->>'id',r->>'policy_reviewed_sha256','approved',(r->>'policy_reviewed_at')::date,'Explicit reviewed source metadata; not crawl date');
  END IF;
 END LOOP;
 RETURN n;
END $fn$;

CREATE OR REPLACE FUNCTION public.ingest_finish_v2(p_secret text,p_run_id text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO '' AS $fn$
DECLARE n integer;
BEGIN
 IF p_secret IS NULL OR p_secret='' OR p_secret IS DISTINCT FROM (SELECT value FROM private.settings WHERE key='ingest_secret') THEN
  RAISE EXCEPTION 'invalid ingest secret';
 END IF;
 UPDATE public.chunks SET status='superseded'
 WHERE run_id<>p_run_id AND run_id<>'announce' AND status='active';
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n;
END $fn$;

-- Rollback: revert the application commit to the original RPC names. Keep review
-- audit/history tables; do not delete chunks or re-enable obsolete policy text.
COMMIT;
