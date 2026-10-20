-- OPTIONAL hardening (not applied automatically).
-- Creates a login role that can only read/write game rows in schema "bluff"
-- (no DDL, no access to other schemas). Use it for DATABASE_URL in production
-- and set AUTO_MIGRATE = "false"; run migrations with the owner credentials.
--
-- 1. Replace CHANGE_ME with a long random password, run this in the Supabase SQL editor.
-- 2. Pooler username is  bluff_app.<project-ref>  (e.g. bluff_app.abcdefghijklmnop).
-- Run it after all migrations (001, 002, ...) so every table is covered; re-running is safe.

do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'bluff_app') then
        create role bluff_app login password 'CHANGE_ME';
    end if;
end $$;

grant usage on schema bluff to bluff_app;
grant select, insert, update, delete on all tables in schema bluff to bluff_app;
grant usage, select on all sequences in schema bluff to bluff_app;
revoke insert, update, delete on bluff.schema_migrations from bluff_app;

-- RLS is enabled on every table; this role gets an explicit allow-all policy.
-- anon/authenticated still have no policy and no grants.
do $$
declare t text;
begin
    foreach t in array array['rooms', 'players', 'inventories', 'player_cards', 'round_events', 'rounds',
                             'market_prices', 'submissions', 'private_log', 'event_log', 'results',
                             'schema_migrations', 'player_orders']
    loop
        execute format('drop policy if exists bluff_app_all on bluff.%I', t);
        execute format('create policy bluff_app_all on bluff.%I for all to bluff_app using (true) with check (true)', t);
    end loop;
end $$;
