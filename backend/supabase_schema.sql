-- MindEase Supabase schema
-- Run this in Supabase SQL editor once.

create extension if not exists "pgcrypto";

create table if not exists public.profiles (
    id uuid primary key default gen_random_uuid(),
    device_id text not null unique,
    name text not null default '',
    avatar text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.journal_entries (
    id uuid primary key default gen_random_uuid(),
    device_id text not null,
    date date not null,
    mood text not null,
    content text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint journal_entries_device_date_unique unique (device_id, date)
);

create table if not exists public.assessment_results (
    id uuid primary key default gen_random_uuid(),
    device_id text not null,
    date date not null,
    score integer not null,
    severity text not null,
    answers jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.liked_quotes (
    id uuid primary key default gen_random_uuid(),
    device_id text not null,
    quote_text text not null,
    quote_author text not null,
    created_at timestamptz not null default now(),
    constraint liked_quotes_device_quote_unique unique (device_id, quote_text)
);

create index if not exists idx_journal_entries_device_date on public.journal_entries (device_id, date desc);
create index if not exists idx_assessment_results_device_created on public.assessment_results (device_id, created_at desc);
create index if not exists idx_liked_quotes_device on public.liked_quotes (device_id);

alter table public.profiles replica identity full;
alter table public.journal_entries replica identity full;
alter table public.assessment_results replica identity full;
alter table public.liked_quotes replica identity full;

do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'profiles'
    ) then
        alter publication supabase_realtime add table public.profiles;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'journal_entries'
    ) then
        alter publication supabase_realtime add table public.journal_entries;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'assessment_results'
    ) then
        alter publication supabase_realtime add table public.assessment_results;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'liked_quotes'
    ) then
        alter publication supabase_realtime add table public.liked_quotes;
    end if;
end $$;
