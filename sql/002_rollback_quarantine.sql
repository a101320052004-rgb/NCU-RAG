-- PREPARED ONLY. Explicit operator approval required. Keeps v2 eligibility active.
-- SET rag_repair.batch='same-approved-batch';
-- Restores status only if body, run and quarantine status remain unchanged.
-- Do not combine with a rollback to legacy retrieval: legacy eligibility could
-- expose old law again. All audit rows/history are retained.
BEGIN;
DO $fn$ BEGIN
 IF nullif(current_setting('rag_repair.batch',true),'') IS NULL THEN
  RAISE EXCEPTION 'Specify reviewed quarantine batch';
 END IF;
END $fn$;
UPDATE public.chunks c SET status=a.original_status
FROM private.rag_repair_quarantine a
WHERE a.batch=current_setting('rag_repair.batch') AND a.chunk_id=c.id
 AND c.status='needs_review' AND c.run_id=a.original_run_id
 AND encode(sha256(convert_to(c.body,'UTF8')),'hex')=a.body_sha256
RETURNING c.id,c.status;
COMMIT;
