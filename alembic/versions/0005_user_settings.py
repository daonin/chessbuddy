from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_settings',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('brilliant_cp', sa.Integer(), nullable=True),
        sa.Column('great_cp', sa.Integer(), nullable=True),
        sa.Column('inaccuracy_cp', sa.Integer(), nullable=True),
        sa.Column('mistake_cp', sa.Integer(), nullable=True),
        sa.Column('blunder_cp', sa.Integer(), nullable=True),
        sa.Column('near_best_tolerance_cp', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('user_id'),
        schema='chessbuddy'
    )
    op.create_foreign_key(
        None,
        'user_settings',
        'users',
        ['user_id'],
        ['id'],
        source_schema='chessbuddy',
        referent_schema='chessbuddy',
        ondelete='CASCADE'
    )


def downgrade() -> None:
    op.drop_constraint(None, 'user_settings', schema='chessbuddy', type_='foreignkey')
    op.drop_table('user_settings', schema='chessbuddy')


