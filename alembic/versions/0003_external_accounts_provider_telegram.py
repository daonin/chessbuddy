"""allow telegram provider in external_accounts

Revision ID: 0003
Revises: 0002
Create Date: 2025-08-09 00:00:01
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r'''
        alter table chessbuddy.external_accounts
        drop constraint if exists external_accounts_provider_check;
        alter table chessbuddy.external_accounts
        add constraint external_accounts_provider_check
        check (provider in ('chess.com','lichess','other','telegram'));
        '''
    )


def downgrade() -> None:
    op.execute(
        r'''
        alter table chessbuddy.external_accounts
        drop constraint if exists external_accounts_provider_check;
        alter table chessbuddy.external_accounts
        add constraint external_accounts_provider_check
        check (provider in ('chess.com','lichess','other'));
        '''
    )
