"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2025-08-09 00:00:00
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r'''
        -- Users
        create table if not exists chessbuddy.users (
          id                bigserial primary key,
          username          text not null unique,
          display_name      text,
          email             text unique,
          created_at        timestamptz not null default now()
        );

        -- External accounts mapping (e.g., chess.com)
        create table if not exists chessbuddy.external_accounts (
          id                bigserial primary key,
          user_id           bigint not null references chessbuddy.users(id) on delete cascade,
          provider          text not null check (provider in ('chess.com','lichess','other')),
          external_username text not null,
          external_user_id  text,
          profile_url       text,
          created_at        timestamptz not null default now(),
          unique (provider, external_username)
        );

        -- Players (any participants in games)
        create table if not exists chessbuddy.players (
          id                bigserial primary key,
          provider          text not null check (provider in ('chess.com','lichess','local','other')),
          username          text not null,
          display_name      text,
          title             text,
          country           text,
          unique (provider, username)
        );

        -- Games
        create table if not exists chessbuddy.games (
          id                bigserial primary key,
          external_source   text check (external_source in ('chess.com','lichess','manual','other')),
          external_game_id  text,
          url               text,
          pgn               text not null,
          pgn_headers       jsonb,
          pgn_sha1          char(40),
          white_player_id   bigint not null references chessbuddy.players(id),
          black_player_id   bigint not null references chessbuddy.players(id),
          white_rating      integer,
          black_rating      integer,
          time_control      text,
          time_class        text,
          rated             boolean,
          termination       text,
          result            text,
          played_at         timestamptz,
          imported_at       timestamptz not null default now(),
          source_raw        jsonb,
          unique (external_source, external_game_id),
          unique (pgn_sha1)
        );

        create index if not exists idx_games_played_at_id on chessbuddy.games (played_at, id);
        create index if not exists idx_games_white on chessbuddy.games (white_player_id);
        create index if not exists idx_games_black on chessbuddy.games (black_player_id);
        create index if not exists idx_games_pgn_headers_gin on chessbuddy.games using gin (pgn_headers);
        create index if not exists idx_games_played_at_brin on chessbuddy.games using brin (played_at) with (pages_per_range = 128);

        -- Moves (half-moves)
        create table if not exists chessbuddy.moves (
          id                bigserial primary key,
          game_id           bigint not null references chessbuddy.games(id) on delete cascade,
          ply               integer not null,
          move_number       integer not null,
          side              char(1) not null check (side in ('w','b')),
          san               text,
          uci               text,
          from_square       char(2),
          to_square         char(2),
          piece             text,
          capture           boolean,
          promotion         char(1),
          is_check          boolean,
          is_checkmate      boolean,
          fen_before        text,
          fen_after         text,
          clock_ms          integer,
          comment           text,
          created_at        timestamptz not null default now(),
          unique (game_id, ply)
        );
        create index if not exists idx_moves_game_ply on chessbuddy.moves (game_id, ply);
        create index if not exists idx_moves_game_move_number on chessbuddy.moves (game_id, move_number);

        -- Engine evaluations per move
        create table if not exists chessbuddy.engine_evaluations (
          id                bigserial primary key,
          game_id           bigint not null references chessbuddy.games(id) on delete cascade,
          move_id           bigint not null references chessbuddy.moves(id) on delete cascade,
          ply               integer not null,
          eval_side         char(1) not null check (eval_side in ('w','b')),
          score_cp          integer,
          score_mate        integer,
          best_move_uci     text,
          pv                text,
          depth             integer,
          nodes             bigint,
          nps               bigint,
          engine_name       text,
          engine_version    text,
          created_at        timestamptz not null default now(),
          unique (move_id, engine_name, depth)
        );
        create index if not exists idx_eval_game_ply on chessbuddy.engine_evaluations (game_id, ply);
        create index if not exists idx_eval_move on chessbuddy.engine_evaluations (move_id);

        -- Categories for special moves
        create table if not exists chessbuddy.move_categories (
          id                smallserial primary key,
          key               text not null unique,
          name              text not null,
          description       text
        );

        -- Highlights: categorized notable moves
        create table if not exists chessbuddy.move_highlights (
          id                bigserial primary key,
          game_id           bigint not null references chessbuddy.games(id) on delete cascade,
          move_id           bigint not null references chessbuddy.moves(id) on delete cascade,
          category_id       smallint not null references chessbuddy.move_categories(id),
          ply               integer not null,
          eval_before_cp    integer,
          eval_after_cp     integer,
          eval_delta_cp     integer,
          comment           text,
          tag               text,
          created_by_user_id bigint references chessbuddy.users(id),
          created_by_model  text,
          created_at        timestamptz not null default now(),
          unique (move_id, category_id)
        );
        create index if not exists idx_highlights_cat_created on chessbuddy.move_highlights (category_id, created_at, id);
        create index if not exists idx_highlights_cat_ply on chessbuddy.move_highlights (category_id, ply);
        create index if not exists idx_highlights_game_ply on chessbuddy.move_highlights (game_id, ply);
        create index if not exists idx_highlights_created_brin on chessbuddy.move_highlights using brin (created_at) with (pages_per_range = 128);
        -- covering index to speed feed queries (PG11+)
        do $$ begin
            begin
                execute 'create index if not exists idx_highlights_cat_feed_cover on chessbuddy.move_highlights (category_id, created_at desc, id desc) include (game_id, ply, eval_delta_cp, tag)';
            exception when others then
                -- ignore if INCLUDE not supported
                null;
            end;
        end $$;

        -- Tactics tasks
        create table if not exists chessbuddy.tactics_tasks (
          id                bigserial primary key,
          user_id           bigint not null references chessbuddy.users(id) on delete cascade,
          game_id           bigint not null references chessbuddy.games(id) on delete cascade,
          move_id           bigint references chessbuddy.moves(id) on delete set null,
          source_highlight_id bigint references chessbuddy.move_highlights(id) on delete set null,
          position_ply      integer not null,
          fen               text not null,
          category_id       smallint references chessbuddy.move_categories(id),
          status            text not null default 'new' check (status in ('new','answered','expired','cancelled')),
          time_limit_ms     integer,
          created_at        timestamptz not null default now(),
          answered_at       timestamptz
        );
        -- expression unique via unique index
        create unique index if not exists uidx_tasks_identity on chessbuddy.tactics_tasks (user_id, game_id, position_ply, coalesce(category_id, 0));
        create index if not exists idx_tasks_user_status on chessbuddy.tactics_tasks (user_id, status, created_at, id);
        create index if not exists idx_tasks_cat_created on chessbuddy.tactics_tasks (category_id, created_at);

        -- User responses
        create table if not exists chessbuddy.tactics_responses (
          id                bigserial primary key,
          task_id           bigint not null references chessbuddy.tactics_tasks(id) on delete cascade,
          user_id           bigint not null references chessbuddy.users(id) on delete cascade,
          proposed_move_uci text,
          proposed_move_san text,
          response_ms       integer,
          evaluated_by_engine boolean not null default true,
          is_correct        boolean,
          score_cp_delta    integer,
          engine_eval_after_cp integer,
          engine_best_move_uci text,
          created_at        timestamptz not null default now()
        );
        create index if not exists idx_responses_task_created on chessbuddy.tactics_responses (task_id, created_at);
        create index if not exists idx_responses_user_created on chessbuddy.tactics_responses (user_id, created_at);

        -- Views
        create or replace view chessbuddy.v_game_meta as
        select
          g.id,
          g.external_source,
          g.external_game_id,
          g.url,
          g.played_at,
          g.time_control,
          g.time_class,
          g.rated,
          g.result,
          wp.username as white_username,
          bp.username as black_username,
          g.white_rating,
          g.black_rating
        from chessbuddy.games g
        join chessbuddy.players wp on wp.id = g.white_player_id
        join chessbuddy.players bp on bp.id = g.black_player_id;

        create or replace view chessbuddy.v_move_highlights_feed as
        select
          h.id as highlight_id,
          h.category_id,
          c.key as category_key,
          h.game_id,
          h.ply,
          h.eval_before_cp,
          h.eval_after_cp,
          h.eval_delta_cp,
          h.tag,
          h.comment,
          g.played_at,
          wp.username as white_username,
          bp.username as black_username
        from chessbuddy.move_highlights h
        join chessbuddy.move_categories c on c.id = h.category_id
        join chessbuddy.games g on g.id = h.game_id
        join chessbuddy.players wp on wp.id = g.white_player_id
        join chessbuddy.players bp on bp.id = g.black_player_id;

        -- Seed default categories
        insert into chessbuddy.move_categories(key, name) values
        ('brilliant','Brilliant'),
        ('great','Great'),
        ('best','Best'),
        ('good','Good'),
        ('inaccuracy','Inaccuracy'),
        ('mistake','Mistake'),
        ('blunder','Blunder'),
        ('missed_win','Missed Win'),
        ('missed_draw','Missed Draw'),
        ('novelty','Novelty')
        on conflict do nothing;
        '''
    )


def downgrade() -> None:
    op.execute(
        r'''
        drop view if exists chessbuddy.v_move_highlights_feed;
        drop view if exists chessbuddy.v_game_meta;

        drop table if exists chessbuddy.tactics_responses;
        drop index if exists chessbuddy.uidx_tasks_identity;
        drop table if exists chessbuddy.tactics_tasks;

        drop table if exists chessbuddy.move_highlights;
        drop table if exists chessbuddy.move_categories;

        drop table if exists chessbuddy.engine_evaluations;
        drop table if exists chessbuddy.moves;
        drop table if exists chessbuddy.games;
        drop table if exists chessbuddy.players;
        drop table if exists chessbuddy.external_accounts;
        drop table if exists chessbuddy.users;

        -- keep schema to preserve alembic_version if configured there
        '''
    )

