-- PREPARED ONLY. Human must compare exact live chunk with current official source.
-- Required settings (never substitute exported/redacted-body hashes):
-- SET rag_repair.chunk_id='reviewed-live-id';
-- SET rag_repair.body_sha256='64-hex-hash-from-live-preflight';
-- SET rag_repair.official_url='https://reviewed-official-source';
-- SET rag_repair.reviewed_at='YYYY-MM-DD'; -- actual HUMAN verification date
BEGIN;
DO $fn$ DECLARE c public.chunks; expected text; reviewed date; source_url text; BEGIN
 expected:=current_setting('rag_repair.body_sha256',true);
 source_url:=current_setting('rag_repair.official_url',true);
 reviewed:=nullif(current_setting('rag_repair.reviewed_at',true),'')::date;
 IF expected IS NULL OR expected !~ '^[a-f0-9]{64}$'
  OR source_url IS NULL OR source_url !~ '^https://'
  OR reviewed IS NULL OR reviewed>(now() AT TIME ZONE 'Asia/Taipei')::date
  OR reviewed<((now() AT TIME ZONE 'Asia/Taipei')::date-interval '1 year')::date THEN
  RAISE EXCEPTION 'Explicit live hash, official source and recent human review required';
 END IF;
 SELECT * INTO c FROM public.chunks WHERE id=current_setting('rag_repair.chunk_id',true) FOR UPDATE;
 IF c.id IS NULL OR c.status<>'active'
  OR encode(sha256(convert_to(c.body,'UTF8')),'hex')<>expected
  OR public.policy_claim_rule(c.body) LIKE 'obsolete:%' THEN
  RAISE EXCEPTION 'Missing/changed/inactive/obsolete chunk; do not approve';
 END IF;
 INSERT INTO public.chunk_policy_reviews(chunk_id,body_sha256,review_status,reviewed_at,note)
 VALUES(c.id,expected,'approved',reviewed,'Human verified against '||source_url);
END $fn$;
COMMIT;
