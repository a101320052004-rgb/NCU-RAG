-- PREPARED ONLY. Operator approval required. First review this SELECT in read-only mode:
-- SELECT id,title,url,run_id,public.policy_effective_rule(c),status,
--        encode(sha256(convert_to(body,'UTF8')),'hex') AS live_body_sha256
-- FROM public.chunks c WHERE status='active' AND public.policy_effective_rule(c) LIKE 'obsolete:%';
-- Export redaction hashes are NOT used. Set a unique batch AFTER reviewing live rows:
-- SET rag_repair.batch='operator-approved-YYYYMMDD-N';
BEGIN;
DO $fn$ BEGIN
 IF nullif(current_setting('rag_repair.batch',true),'') IS NULL THEN
  RAISE EXCEPTION 'Set operator-approved rag_repair.batch after live preflight';
 END IF;
END $fn$;
CREATE TABLE IF NOT EXISTS private.rag_repair_quarantine (
 batch text NOT NULL, chunk_id text NOT NULL, body_sha256 text NOT NULL,
 original_status text NOT NULL, original_run_id text NOT NULL,
 rule text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(batch,chunk_id)
);
ALTER TABLE private.rag_repair_quarantine ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON private.rag_repair_quarantine FROM PUBLIC;
INSERT INTO private.rag_repair_quarantine(batch,chunk_id,body_sha256,original_status,original_run_id,rule)
SELECT current_setting('rag_repair.batch'),id,encode(sha256(convert_to(body,'UTF8')),'hex'),status,run_id,
 public.policy_effective_rule(c) FROM public.chunks c
WHERE status='active' AND public.policy_effective_rule(c) LIKE 'obsolete:%'
ON CONFLICT DO NOTHING;
UPDATE public.chunks c SET status='needs_review'
FROM private.rag_repair_quarantine a
WHERE a.batch=current_setting('rag_repair.batch') AND a.chunk_id=c.id
 AND c.status=a.original_status AND c.run_id=a.original_run_id
 AND encode(sha256(convert_to(c.body,'UTF8')),'hex')=a.body_sha256
 AND public.policy_effective_rule(c) LIKE 'obsolete:%'
RETURNING c.id,c.status,a.rule;
COMMIT;
