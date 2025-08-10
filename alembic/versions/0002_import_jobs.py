"""import jobs tracking

Revision ID: 0002
Revises: 0001
Create Date: 2025-08-09 00:00:00
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r'''
        create table if not exists chessbuddy.import_jobs (
          id bigserial primary key,
          provider text not null check (provider in ('chess.com')),
          username text not null,
          initiated_by_user_id bigint references chessbuddy.users(id),
          started_at timestamptz not null default now(),
          finished_at timestamptz,
          total_months integer,
          processed_months integer not null default 0,
          total_games integer not null default 0,
          imported_games integer not null default 0,
          skipped_games integer not null default 0,
          status text not null default 'new' check (status in ('new','running','done','failed')),
          error text
        );
        create index if not exists idx_import_jobs_user_time on chessbuddy.import_jobs (username, started_at desc);
        '''
    )


def downgrade() -> None:
    op.execute(
        r'''
        drop table if exists chessbuddy.import_jobs;
        '''
    )
