-- 追問用的前幾題：只取 30 分鐘內的，並包含選單點選與查無資料的題目（2026-11-11 已套用到 Supabase）
create or replace function public.recent_turns_v2(p_app_secret text, p_user_hash text, p_n integer default 3,
                                                  p_minutes integer default 30)
returns table(question text, answer text)
language plpgsql stable security definer set search_path to ''
as $function$
begin
  perform private.check_app(p_app_secret);
  return query select t.question, t.answer from (
    select m.id, m.question, m.answer from public.messages m
    where m.user_hash = p_user_hash
      and m.route in ('rag', 'menu', 'no_data')
      and m.created_at > now() - make_interval(mins => least(greatest(p_minutes, 1), 240))
    order by m.id desc limit least(p_n, 5)) t
  order by t.id;
end $function$;
grant execute on function public.recent_turns_v2(text, text, integer, integer) to anon, authenticated, service_role;
