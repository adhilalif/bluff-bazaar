-- Bluff Bazaar schema. Idempotent: safe to run more than once.
-- All game tables live in schema "bluff", which is NOT exposed through the
-- Supabase Data API. Only the trusted Streamlit server connects to it.

create extension if not exists pgcrypto;  -- gen_random_uuid() on older Postgres

create schema if not exists bluff;

create table if not exists bluff.schema_migrations (
    filename   text primary key,
    applied_at timestamptz not null default now()
);

create table if not exists bluff.rooms (
    id               uuid primary key default gen_random_uuid(),
    code             char(6) not null unique check (code ~ '^[A-Z0-9]{6}$'),
    status           text not null default 'lobby'
                     check (status in ('lobby', 'active', 'finished', 'abandoned')),
    config           jsonb not null,
    seed             bigint not null,
    current_round    smallint not null default 0 check (current_round >= 0),
    round_deadline   timestamptz,
    version          bigint not null default 0,
    created_at       timestamptz not null default now(),
    last_activity_at timestamptz not null default now(),
    finished_at      timestamptz
);
create index if not exists rooms_status_activity_idx on bluff.rooms (status, last_activity_at);

create table if not exists bluff.players (
    id           uuid primary key default gen_random_uuid(),
    room_id      uuid not null references bluff.rooms (id) on delete cascade,
    seat         smallint not null check (seat between 1 and 4),
    display_name text not null check (char_length(display_name) between 1 and 20),
    token_hash   text not null unique check (char_length(token_hash) = 64),
    is_host      boolean not null default false,
    is_ready     boolean not null default false,
    coins        integer not null check (coins >= 0),
    joined_at    timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    unique (room_id, seat)
);
create unique index if not exists players_room_name_uq on bluff.players (room_id, lower(display_name));

create table if not exists bluff.inventories (
    player_id uuid not null references bluff.players (id) on delete cascade,
    commodity text not null check (commodity in ('crystals', 'spices', 'energy', 'metals', 'relics')),
    quantity  integer not null default 0 check (quantity >= 0),
    primary key (player_id, commodity)
);

create table if not exists bluff.player_cards (
    id           uuid primary key default gen_random_uuid(),
    room_id      uuid not null references bluff.rooms (id) on delete cascade,
    player_id    uuid not null references bluff.players (id) on delete cascade,
    card_type    text not null check (card_type in
                 ('market_pump', 'market_crash', 'shield', 'insider_tip', 'tax_collector', 'double_down')),
    played_round smallint
);
-- At most one card per player per round, enforced by the database.
create unique index if not exists player_cards_one_per_round
    on bluff.player_cards (player_id, played_round) where played_round is not null;
create index if not exists player_cards_player_idx on bluff.player_cards (player_id);

create table if not exists bluff.round_events (
    room_id   uuid not null references bluff.rooms (id) on delete cascade,
    round_no  smallint not null check (round_no >= 1),
    event_key text not null,
    primary key (room_id, round_no)
);

create table if not exists bluff.rounds (
    room_id     uuid not null references bluff.rooms (id) on delete cascade,
    round_no    smallint not null check (round_no >= 1),
    status      text not null default 'open' check (status in ('open', 'resolved')),
    started_at  timestamptz not null default now(),
    deadline_at timestamptz not null,
    resolved_at timestamptz,
    summary     jsonb,
    primary key (room_id, round_no)
);

-- Price at the START of each round; round (num_rounds + 1) holds final prices.
create table if not exists bluff.market_prices (
    room_id   uuid not null references bluff.rooms (id) on delete cascade,
    round_no  smallint not null check (round_no >= 1),
    commodity text not null,
    price     integer not null check (price > 0),
    primary key (room_id, round_no, commodity)
);

create table if not exists bluff.submissions (
    id               uuid primary key default gen_random_uuid(),
    room_id          uuid not null references bluff.rooms (id) on delete cascade,
    round_no         smallint not null,
    player_id        uuid not null references bluff.players (id) on delete cascade,
    request_id       uuid not null unique,
    action           text not null check (action in ('buy', 'sell', 'hold')),
    commodity        text,
    quantity         smallint not null default 0 check (quantity >= 0),
    card_id          uuid references bluff.player_cards (id),
    card_type        text,
    target_commodity text,
    target_player_id uuid references bluff.players (id) on delete set null,
    is_auto          boolean not null default false,
    created_at       timestamptz not null default now(),
    unique (room_id, round_no, player_id),
    check ((action = 'hold' and commodity is null and quantity = 0)
        or (action in ('buy', 'sell') and commodity is not null and quantity > 0))
);

create table if not exists bluff.private_log (
    id         bigserial primary key,
    room_id    uuid not null references bluff.rooms (id) on delete cascade,
    player_id  uuid not null references bluff.players (id) on delete cascade,
    round_no   smallint not null,
    kind       text not null,
    payload    jsonb not null,
    request_id uuid unique,
    created_at timestamptz not null default now()
);
create index if not exists private_log_player_idx on bluff.private_log (player_id, id);

create table if not exists bluff.event_log (
    id         bigserial primary key,
    room_id    uuid not null references bluff.rooms (id) on delete cascade,
    round_no   smallint not null default 0,
    kind       text not null,
    payload    jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists event_log_room_idx on bluff.event_log (room_id, id);

create table if not exists bluff.results (
    room_id         uuid not null references bluff.rooms (id) on delete cascade,
    player_id       uuid not null references bluff.players (id) on delete cascade,
    rank            smallint not null,
    net_worth       integer not null,
    coins           integer not null,
    inventory_value integer not null,
    primary key (room_id, player_id)
);

-- ---------------------------------------------------------------------------
-- Lock down: RLS on with NO policies, and no privileges for API roles.
-- The Streamlit server connects as the schema owner, which bypasses RLS.
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
    foreach t in array array['schema_migrations', 'rooms', 'players', 'inventories', 'player_cards',
                             'round_events', 'rounds', 'market_prices', 'submissions',
                             'private_log', 'event_log', 'results']
    loop
        execute format('alter table bluff.%I enable row level security', t);
    end loop;

    revoke all on schema bluff from public;
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on schema bluff from anon';
        execute 'revoke all on all tables in schema bluff from anon';
        execute 'revoke all on all sequences in schema bluff from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke all on schema bluff from authenticated';
        execute 'revoke all on all tables in schema bluff from authenticated';
        execute 'revoke all on all sequences in schema bluff from authenticated';
    end if;
end $$;
