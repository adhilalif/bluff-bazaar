-- Full Bazaar rules: cargo bids, secret merchant orders, order bonuses. Additive and idempotent.

alter table bluff.submissions add column if not exists bid integer not null default 0 check (bid >= 0);

alter table bluff.results add column if not exists order_bonus integer not null default 0;

-- One secret merchant order per player (Full Bazaar rooms only). Private until the game ends.
create table if not exists bluff.player_orders (
    player_id uuid primary key references bluff.players (id) on delete cascade,
    room_id   uuid not null references bluff.rooms (id) on delete cascade,
    order_key text not null
);
create index if not exists player_orders_room_idx on bluff.player_orders (room_id);

-- Same lock-down as every other game table: RLS on, no policies, no API-role privileges.
alter table bluff.player_orders enable row level security;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on bluff.player_orders from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke all on bluff.player_orders from authenticated';
    end if;
    -- If the optional least-privilege role exists, extend its access to the new table.
    if exists (select 1 from pg_roles where rolname = 'bluff_app') then
        execute 'grant select, insert, update, delete on bluff.player_orders to bluff_app';
        execute 'drop policy if exists bluff_app_all on bluff.player_orders';
        execute 'create policy bluff_app_all on bluff.player_orders for all to bluff_app using (true) with check (true)';
    end if;
end $$;
