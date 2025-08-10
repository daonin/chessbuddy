from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004_players_link_user_id'
down_revision = '0003_external_accounts_provider_telegram'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Add user_id to players and backfill from external_accounts
    op.add_column('players', sa.Column('user_id', sa.BigInteger(), nullable=True, schema='chessbuddy'))
    op.create_foreign_key(None, 'players', 'users', ['user_id'], ['id'], source_schema='chessbuddy', referent_schema='chessbuddy', ondelete='SET NULL')

    conn.execute(sa.text(
        """
        update chessbuddy.players p
        set user_id = ea.user_id
        from chessbuddy.external_accounts ea
        where ea.provider = p.provider and ea.external_username = p.username
        """
    ))


def downgrade() -> None:
    op.drop_constraint(None, 'players', schema='chessbuddy', type_='foreignkey')
    op.drop_column('players', 'user_id', schema='chessbuddy')


