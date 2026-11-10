-- NOT EXECUTED HERE. Run with psql -v ON_ERROR_STOP=1 after 001 in an isolated
-- clone whose database name ends in _test. Uses rollback; never target production.
BEGIN;
DO $fn$ BEGIN
 IF current_database() !~ '_test$' THEN RAISE EXCEPTION 'Isolated _test database required'; END IF;
 IF EXISTS(SELECT 1 FROM public.chunks WHERE id LIKE 'repair-isolated-%') THEN
  RAISE EXCEPTION 'Fixture IDs already exist'; END IF;
END $fn$;
INSERT INTO public.chunks(id,run_id,body,topics,identities,status,last_verified,embedding)
SELECT 'repair-isolated-'||label,'repair-test',body,ARRAY['住'],identities,status,
 (now() AT TIME ZONE 'Asia/Taipei')::date,
 (ARRAY[1.0]||array_fill(0.0,ARRAY[383]))::extensions.vector
FROM (VALUES
 ('safe','Dormitory contacts',ARRAY['交換生'],'active'),
 ('review','Dormitory contacts',ARRAY['交換生'],'needs_review'),
 ('wrong-id','Dormitory contacts',ARRAY['陸生'],'active'),
 ('expired','Dormitory contacts',ARRAY['交換生'],'active'),
 ('arc-old','ARC apply within 15 days after arrival.',ARRAY['交換生'],'active'),
 ('work-old','Work permit maximum duration is 6 months.',ARRAY['交換生'],'active'),
 ('current','ARC apply within 30 days after arrival.',ARRAY['交換生'],'active')
) t(label,body,identities,status);
UPDATE public.chunks SET valid_until=(now() AT TIME ZONE 'Asia/Taipei')::date-1
WHERE id='repair-isolated-expired';
DO $fn$ DECLARE r public.chunks; found_ids text[]; BEGIN
 SELECT * INTO r FROM public.chunks WHERE id='repair-isolated-safe';
 IF NOT public.chunk_is_eligible(r,'交換生','住',CURRENT_DATE-365) THEN RAISE EXCEPTION 'Safe denied'; END IF;
 FOR r IN SELECT * FROM public.chunks WHERE id LIKE 'repair-isolated-%' AND id<>'repair-isolated-safe' LOOP
  IF public.chunk_is_eligible(r,'交換生','住',CURRENT_DATE-365) THEN RAISE EXCEPTION 'Unsafe allowed: %',r.id; END IF;
 END LOOP;
 SELECT array_agg(j->>'id') INTO found_ids FROM public.get_chunks_v2(
  ARRAY(SELECT id FROM public.chunks WHERE id LIKE 'repair-isolated-%'),'交換生','住',CURRENT_DATE-365) j;
 IF found_ids IS DISTINCT FROM ARRAY['repair-isolated-safe'] THEN RAISE EXCEPTION 'Neighbors bypassed eligibility'; END IF;
 INSERT INTO public.chunk_policy_reviews(chunk_id,body_sha256,review_status,reviewed_at,note)
 SELECT id,encode(sha256(convert_to(body,'UTF8')),'hex'),'approved',CURRENT_DATE,'ISOLATED TEST ONLY'
 FROM public.chunks WHERE id='repair-isolated-current';
 SELECT * INTO r FROM public.chunks WHERE id='repair-isolated-current';
 IF NOT public.chunk_is_eligible(r,'交換生','住',CURRENT_DATE-365) THEN RAISE EXCEPTION 'Approved current denied'; END IF;
 UPDATE public.chunks SET body=body||' Changed' WHERE id=r.id;
 SELECT * INTO r FROM public.chunks WHERE id=r.id;
 IF public.chunk_is_eligible(r,'交換生','住',CURRENT_DATE-365) THEN RAISE EXCEPTION 'Changed body reused approval'; END IF;
 IF NOT EXISTS(SELECT 1 FROM private.chunk_version_history WHERE chunk_id=r.id) THEN RAISE EXCEPTION 'History missing'; END IF;
END $fn$;
ROLLBACK;
