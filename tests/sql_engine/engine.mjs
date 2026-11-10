import {PGlite} from '@electric-sql/pglite';
import {vector} from '@electric-sql/pglite-pgvector';
import fs from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline';

const repo=process.argv[2];
const packageDir=process.argv[3];
const mode=process.argv[4]||'checks';
if(!repo||!packageDir) throw Error('Usage: node engine.mjs <repository> <diagnostic-package> [checks|bridge]');
let seed=await PGlite.create({extensions:{vector}});
await seed.exec('CREATE DATABASE ncu_reliability_test');
const dump=await seed.dumpDataDir();
await seed.close();
const pg=await PGlite.create({extensions:{vector},database:'ncu_reliability_test',loadDataDir:dump});
await pg.exec("SET TIME ZONE 'Asia/Taipei'; CREATE SCHEMA extensions; CREATE EXTENSION vector WITH SCHEMA extensions; CREATE SCHEMA private; CREATE TABLE private.settings(key text PRIMARY KEY,value text NOT NULL); INSERT INTO private.settings VALUES('ingest_secret','isolated-only');");
const schema=await fs.readFile(path.join(packageDir,'database/schema.sql'),'utf8');
const chunks=schema.match(/CREATE TABLE public\.chunks \([\s\S]*?\n\);/)[0];
await pg.exec(chunks+' ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;');
const funcs=await fs.readFile(path.join(packageDir,'database/retrieval_functions.sql'),'utf8');
await pg.exec('SET search_path=public,extensions;');
for(const name of ['match_chunks','get_chunks','find_partner_chunks','ingest_upsert']){
 const start=funcs.indexOf('CREATE OR REPLACE FUNCTION public.'+name+'(');
 const end=funcs.indexOf('$function$;',start)+'$function$;'.length;
 if(start<0||end<start)throw Error('Missing baseline '+name);
 await pg.exec(funcs.slice(start,end));
}
const migration=await fs.readFile(path.join(repo,'sql/001_reliability_v2.sql'),'utf8');
await pg.exec(migration);
await pg.exec(migration); // migration is rerunnable
const versions=(await pg.query("SELECT version(),current_database(),extversion FROM pg_extension WHERE extname='vector'")).rows[0];

async function rpc(name,args){
 const version=name.endsWith('_v2');
 if(name==='match_chunks'||name==='match_chunks_v2'){
  const result=await pg.query(`SELECT ${version?'j AS value':'to_jsonb(j) AS value'} FROM public.${name}($1::extensions.vector,$2::int,$3::text,$4::text,$5::date) j`,[args.query_embedding,args.match_count,args.p_identity,args.p_topic,args.p_stale_before]);
  return result.rows.map(r=>r.value);
 }
 if(name==='get_chunks_v2'||name==='find_partner_chunks_v2'){
  const result=await pg.query(`SELECT j AS value FROM public.${name}($1::text[],$2::text,$3::text,$4::date) j`,[args.p_ids||args.p_terms,args.p_identity,args.p_topic,args.p_stale_before]);
  return result.rows.map(r=>r.value);
 }
 if(name==='get_chunks'||name==='find_partner_chunks'){
  const result=await pg.query(`SELECT to_jsonb(j) AS value FROM public.${name}($1::text[]) j`,[args.p_ids||args.p_terms]);
  return result.rows.map(r=>r.value);
 }
 throw Error('RPC not permitted in snapshot bridge: '+name);
}

async function insertRows(rows){
 return await pg.query(`INSERT INTO public.chunks(id,run_id,source,title,url,lang,body,topics,identities,status,valid_until,last_verified,embedding)
 SELECT id,'isolated-snapshot',source,title,url,lang,body,topics,identities,status,valid_until::date,last_verified::date,embedding::extensions.vector
 FROM jsonb_to_recordset($1::jsonb) r(id text,source text,title text,url text,lang text,body text,topics text[],identities text[],status text,valid_until text,last_verified text,embedding text)`,[JSON.stringify(rows)]);
}

if(mode==='bridge'){
 console.log(JSON.stringify({ready:true,versions}));
 const rl=readline.createInterface({input:process.stdin});
 for await (const line of rl){
  try{
   const item=JSON.parse(line);
   let value;
   if(item.op==='seed') {await insertRows(item.rows);value={seeded:item.rows.length};}
   else if(item.op==='rpc')value=await rpc(item.name,item.args);
   else if(item.op==='close'){console.log(JSON.stringify({ok:true,value:'closed'}));break;}
   else throw Error('Operation not permitted');
   console.log(JSON.stringify({ok:true,value}));
  }catch(error){console.log(JSON.stringify({ok:false,error:error.message,code:error.code}));}
 }
}else{
 const checks=[];
 const check=async(name,fn)=>{await fn();checks.push({name,passed:true});};
 const assert=(condition,label)=>{if(!condition)throw Error(label);};
 const expectFailure=async(sql,params)=>{let failed=false;try{await pg.query(sql,params);}catch(error){failed=true;}assert(failed,'Expected database rejection');};
 await check('migration_rerunnable_and_F05_F08_native_predicates',async()=>{
   await pg.exec(await fs.readFile(path.join(repo,'sql/003_isolated_checks.sql'),'utf8'));
 });
 const v='['+[1,...Array(383).fill(0)].join(',')+']';
 const today=(await pg.query('SELECT CURRENT_DATE::text AS d')).rows[0].d;
 const r=(id,body='Dormitory contact',more={})=>({id,body,source:'isolated-only',title:'Isolated fixture',url:'https://example.edu',lang:'en',topics:['住'],identities:['交換生'],status:'active',valid_until:null,last_verified:today,embedding:v,...more});
 await insertRows([r('native-safe'),r('native-other','Other',{identities:['陸生']}),r('native-review','Review',{status:'needs_review'}),r('native-old-a','ARC apply within 15 days after arrival.'),r('native-old-b','Work permit maximum duration is 6 months.'),r('sheet-partners-isolated-0','Example University',{identities:['陸生']})]);
 const args={query_embedding:v,match_count:15,p_identity:'交換生',p_topic:'住',p_stale_before:'2000-01-01'};
 await check('main_neighbor_and_partner_share_qualification',async()=>{
  const main=await rpc('match_chunks_v2',args);
  assert(main.length===1&&main[0].id==='native-safe','Main eligibility mismatch');
  const neighbors=await rpc('get_chunks_v2',{...args,p_ids:['native-safe','native-other','native-review','native-old-a']});
  assert(neighbors.length===1&&neighbors[0].id==='native-safe','Neighbor eligibility mismatch');
  const partner=await rpc('find_partner_chunks_v2',{...args,p_terms:['Example University']});
  assert(partner.length===0,'Partner identity leak');
 });
 await check('ingest_secret_and_approval_guard_atomicity',async()=>{
  for(const secret of [null,'','wrong'])await expectFailure('SELECT public.ingest_upsert_v2($1,$2,$3::jsonb)',[secret,'test',JSON.stringify([])]);
  const bad={...r('native-reject','Work permit maximum duration is 6 months.'),policy_review_status:'approved',policy_reviewed_at:today,policy_reviewed_sha256:'0'.repeat(64)};
  await expectFailure('SELECT public.ingest_upsert_v2($1,$2,$3::jsonb)',['isolated-only','test',JSON.stringify([bad])]);
  assert((await pg.query("SELECT count(*)::int AS n FROM public.chunks WHERE id='native-reject'")).rows[0].n===0,'Failed approval left partial writes');
 });
 await check('history_retains_replaced_bodies_and_finish_archives',async()=>{
  await pg.query('SELECT public.ingest_upsert_v2($1,$2,$3::jsonb)',['isolated-only','new-run',JSON.stringify([r('native-safe','Updated official body')])]);
  assert((await pg.query("SELECT count(*)::int AS n FROM private.chunk_version_history WHERE chunk_id='native-safe' AND snapshot->>'body'='Dormitory contact'")).rows[0].n===1,'Prior body missing');
  await pg.query('SELECT public.ingest_finish_v2($1,$2)',['isolated-only','new-run']);
  assert((await pg.query("SELECT status FROM public.chunks WHERE id='native-old-a'")).rows[0].status==='superseded','Old row deleted or not archived');
 });
 await check('quarantine_and_hash_guarded_rollback',async()=>{
  await pg.exec("UPDATE public.chunks SET status='active' WHERE id IN ('native-old-a','native-old-b'); SET rag_repair.batch='isolated-approved-batch';");
  await pg.exec(await fs.readFile(path.join(repo,'sql/002_quarantine_obsolete.sql'),'utf8'));
  assert((await pg.query("SELECT count(*)::int AS n FROM public.chunks WHERE id IN ('native-old-a','native-old-b') AND status='needs_review'")).rows[0].n===2,'Quarantine failed');
  await pg.exec("UPDATE public.chunks SET body=body||' updated' WHERE id='native-old-b';");
  await pg.exec(await fs.readFile(path.join(repo,'sql/002_rollback_quarantine.sql'),'utf8'));
  const rows=(await pg.query("SELECT id,status FROM public.chunks WHERE id IN ('native-old-a','native-old-b') ORDER BY id")).rows;
  assert(rows[0].status==='active'&&rows[1].status==='needs_review','CAS rollback overwrote changed row');
  assert((await rpc('get_chunks_v2',{...args,p_ids:['native-old-a']})).length===0,'Rollback exposed obsolete rule');
 });
 await check('explicit_review_template_and_body_change_invalidation',async()=>{
  await insertRows([r('native-current','ARC apply within 30 days after arrival.')]);
  const hash=(await pg.query("SELECT encode(sha256(convert_to(body,'UTF8')),'hex') AS h FROM public.chunks WHERE id='native-current'")).rows[0].h;
  for(const [key,value] of [['chunk_id','native-current'],['body_sha256',hash],['official_url','https://example.edu/isolated-fixture-only'],['reviewed_at',today]])
   await pg.query('SELECT set_config($1,$2,false)',['rag_repair.'+key,value]);
  await pg.exec(await fs.readFile(path.join(repo,'sql/004_approve_policy_template.sql'),'utf8'));
  assert((await rpc('get_chunks_v2',{...args,p_ids:['native-current']})).length===1,'Reviewed body denied');
  await pg.exec("UPDATE public.chunks SET body=body||' changed' WHERE id='native-current'");
  assert((await rpc('get_chunks_v2',{...args,p_ids:['native-current']})).length===0,'Changed body kept approval');
 });
 await check('split_legacy_arc_fragment_cannot_escape_policy_eligibility',async()=>{
  await insertRows([r('fragment-0','ARC apply within 15 days after arrival.'),r('fragment-1','n 15 days. Required documents.'),r('mdfaq-0','ARC apply within 15 days after arrival.'),r('mdfaq-1','A separate complete FAQ answer')]);
  assert((await pg.query("SELECT public.policy_claim_rule(body) AS rule FROM public.chunks WHERE id='fragment-1'")).rows[0].rule===null,'Fixture not split');
  assert((await rpc('get_chunks_v2',{...args,p_ids:['fragment-1']})).length===0,'Legacy date fragment leaked');
  assert((await rpc('get_chunks_v2',{...args,p_ids:['mdfaq-1']})).length===1,'Independent FAQ incorrectly inherited another question');
  await pg.exec("SET rag_repair.batch='isolated-fragment-batch';");
  await pg.exec(await fs.readFile(path.join(repo,'sql/002_quarantine_obsolete.sql'),'utf8'));
  assert((await pg.query("SELECT status FROM public.chunks WHERE id='fragment-1'")).rows[0].status==='needs_review','Quarantine omitted split legacy fragment');
  assert((await pg.query("SELECT status FROM public.chunks WHERE id='mdfaq-1'")).rows[0].status==='active','Quarantine touched unrelated FAQ');
 });
 await check('anon_direct_read_blocked_but_filtered_rpc_available',async()=>{
  await pg.exec('CREATE ROLE isolated_anon NOLOGIN; GRANT USAGE ON SCHEMA public,extensions TO isolated_anon; GRANT SELECT ON public.chunks TO isolated_anon; SET ROLE isolated_anon;');
  assert((await pg.query('SELECT count(*)::int AS n FROM public.chunks')).rows[0].n===0,'RLS bypass via direct table');
  assert((await rpc('get_chunks_v2',{...args,p_ids:['native-safe']})).length===1,'Filtered RPC unavailable');
  await pg.exec('RESET ROLE;');
 });
 const result={engine:'in-memory PostgreSQL WASM (PGlite), not Supabase/PostgREST',versions,checks,production_connections:0};
 await fs.writeFile(path.join(repo,'repair_evidence/sql_engine_results.json'),JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result));
}
await pg.close();
