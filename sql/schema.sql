-- ============================================================
--  Telegram Mini App «Проверка путевой документации»
--  Схема базы данных (PostgreSQL / Supabase)
--  Выполнить в Supabase → SQL Editor целиком.
-- ============================================================

-- Расширение для хеширования кодов доступа (bcrypt)
create extension if not exists pgcrypto;

-- ---------- Машины и нормы расхода ----------
create table if not exists vehicles (
    id              bigserial primary key,
    plate           text not null unique,                  -- 4-значный ГРЗ, он же код входа водителя
    model           text not null default '',
    fuel_norm       numeric(6,2)  not null default 0,      -- л / 100 км
    motohour_norm   numeric(6,2)  not null default 0,      -- л / моточас
    tank_capacity   numeric(6,2)  not null default 0,
    is_active       boolean not null default true,

    -- сведения о машине (заполняет техник роты)
    vin             text,
    engine_no       text,
    chassis_no      text,
    driver_name     text,
    driver_license  text,
    sts_expires              date,                         -- СТС
    diagnostic_card_expires  date,                         -- диагностическая карта
    red_stripe_expires       date,                         -- красная полоса

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ---------- Отчётные периоды (месяцы) ----------
-- Привязка путевого к отчёту идёт по дате: start_date <= дата <= end_date.
-- Периоды можно задавать с нестандартными границами (по приказу).
create table if not exists periods (
    id          bigserial primary key,
    name        text not null unique,                      -- "Октябрь 26"
    year        int  not null,
    month       int  not null,                             -- номер месяца отчёта (10 = октябрь)
    start_date  date not null,
    end_date    date not null,
    is_open     boolean not null default true,
    created_at  timestamptz not null default now(),
    constraint periods_range_ck check (start_date <= end_date)
);

-- ---------- Пользователи Telegram ----------
create table if not exists app_users (
    id            bigserial primary key,
    telegram_id   bigint not null unique,
    username      text,
    first_name    text,
    last_name     text,
    role          text not null default 'driver',          -- driver | owner | commander | technician
    vehicle_plate text,                                    -- последняя машина водителя
    created_at    timestamptz not null default now(),
    last_seen_at  timestamptz not null default now()
);

-- ---------- Коды доступа админов (3 роли) ----------
-- Коды хранятся в виде хеша, в открытом виде нигде нет.
create table if not exists access_codes (
    role        text primary key,                          -- owner | commander | technician
    code_hash   text not null,
    updated_at  timestamptz not null default now()
);

-- ---------- Путевые листы ----------
create table if not exists waybills (
    id            bigserial primary key,
    period_id     bigint not null references periods(id),
    vehicle_id    bigint not null references vehicles(id),
    plate         text not null,
    model         text not null default '',
    waybill_no    text not null,

    -- данные, введённые водителем
    km_start      numeric(10,1) not null default 0,
    km_end        numeric(10,1) not null default 0,
    km_empty      numeric(10,1) not null default 0,
    km_loaded     numeric(10,1) not null default 0,
    tank_start    numeric(8,1)  not null default 0,
    fuel_in       numeric(8,1)  not null default 0,
    tank_end      numeric(8,1)  not null default 0,
    fuel_spent    numeric(8,1)  not null default 0,
    motohours     numeric(8,1)  not null default 0,

    -- снимок норм на момент расчёта (чтобы прошлые расчёты не «поехали»)
    fuel_norm_snapshot     numeric(6,2) not null default 0,
    motohour_norm_snapshot numeric(6,2) not null default 0,

    -- вычисленные значения
    p1             numeric(10,1) not null default 0,        -- без груза, -15%
    p2             numeric(10,1) not null default 0,        -- с грузом
    h              numeric(10,1) not null default 0,        -- моточасы
    fuel_calc      numeric(10,1) not null default 0,        -- P = p1+p2+h
    km_total_odo   numeric(10,1) not null default 0,
    km_total_input numeric(10,1) not null default 0,
    tank_end_calc  numeric(10,1) not null default 0,

    -- результат проверки
    km_ok          boolean not null default false,
    tank_ok        boolean not null default false,
    fuel_ok        boolean not null default false,
    is_ok          boolean not null default false,
    status         text not null default 'submitted',       -- submitted | approved | rejected

    created_by_telegram_id bigint,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),

    unique (period_id, waybill_no)                          -- номер путевого уникален в рамках периода
);

create index if not exists idx_waybills_period  on waybills(period_id);
create index if not exists idx_waybills_vehicle on waybills(vehicle_id);
create index if not exists idx_waybills_plate   on waybills(plate);
create index if not exists idx_waybills_created on waybills(created_at desc);

-- ---------- Журнал активности (кто что сделал) ----------
create table if not exists activity_log (
    id          bigserial primary key,
    telegram_id bigint,
    actor_name  text,
    role        text,
    action      text not null,                              -- login, create_waybill, update_vehicle, ...
    entity      text,                                       -- waybill | vehicle | period | document
    entity_id   text,
    details     jsonb,
    created_at  timestamptz not null default now()
);
create index if not exists idx_activity_created on activity_log(created_at desc);

-- ---------- Задел на будущее: документы на машину ----------
create table if not exists vehicle_documents (
    id           bigserial primary key,
    vehicle_id   bigint not null references vehicles(id) on delete cascade,
    doc_type     text not null,                             -- СТС, страховка, ТО, ...
    doc_number   text,
    issued_at    date,
    expires_at   date,
    file_url     text,
    note         text,
    created_by_telegram_id bigint,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_documents_vehicle on vehicle_documents(vehicle_id);
create index if not exists idx_documents_expires on vehicle_documents(expires_at);

-- ---------- Задел на будущее: текущее состояние машины ----------
create table if not exists vehicle_state (
    vehicle_id     bigint primary key references vehicles(id) on delete cascade,
    current_mileage numeric(10,1) not null default 0,
    current_fuel    numeric(8,1)  not null default 0,
    updated_at      timestamptz not null default now()
);

-- ============================================================
--  Защита данных (RLS)
--  Backend подключается как postgres — RLS обходит.
--  Публичный API-ключ (anon) доступа к таблицам не получает.
--  Политики не создаём: без политик доступ запрещён.
-- ============================================================
alter table vehicles          enable row level security;
alter table periods           enable row level security;
alter table app_users         enable row level security;
alter table access_codes      enable row level security;
alter table waybills          enable row level security;
alter table activity_log      enable row level security;
alter table vehicle_documents enable row level security;
alter table vehicle_state     enable row level security;

-- ============================================================
--  Дополнения к существующей базе (идемпотентно)
-- ============================================================
alter table vehicles add column if not exists vin            text;
alter table vehicles add column if not exists engine_no      text;
alter table vehicles add column if not exists chassis_no     text;
alter table vehicles add column if not exists driver_name    text;
alter table vehicles add column if not exists driver_license text;
alter table vehicles add column if not exists sts_expires             date;
alter table vehicles add column if not exists diagnostic_card_expires date;
alter table vehicles add column if not exists red_stripe_expires      date;
