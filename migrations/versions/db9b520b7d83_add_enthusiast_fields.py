"""Add Enthusiast fields

Revision ID: db9b520b7d83
Revises: 6d7a7a748362
Create Date: 2026-08-12 19:24:18.356579

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'db9b520b7d83'
down_revision = '6d7a7a748362'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('enthusiasts', sa.Column('username', sa.String(length=50), nullable=False))
    op.add_column('enthusiasts', sa.Column('display_name', sa.String(length=100), nullable=False))
    op.add_column('enthusiasts', sa.Column('operator', sa.String(length=100)))
    op.add_column('enthusiasts', sa.Column('vehicle_number', sa.String(length=50)))
    op.add_column('enthusiasts', sa.Column('registration', sa.String(length=20)))
    op.add_column('enthusiasts', sa.Column('make_model', sa.String(length=100)))
    op.add_column('enthusiasts', sa.Column('depot', sa.String(length=100)))
    op.add_column('enthusiasts', sa.Column('route_number', sa.String(length=20)))
    op.add_column('enthusiasts', sa.Column('notes', sa.Text))
    op.add_column('enthusiasts', sa.Column('spotted_at', sa.DateTime))
    op.add_column('enthusiasts', sa.Column('photos_uploaded', sa.Integer, server_default="0"))

def downgrade():
    op.drop_column('enthusiasts', 'username')
    op.drop_column('enthusiasts', 'display_name')
    op.drop_column('enthusiasts', 'operator')
    op.drop_column('enthusiasts', 'vehicle_number')
    op.drop_column('enthusiasts', 'registration')
    op.drop_column('enthusiasts', 'make_model')
    op.drop_column('enthusiasts', 'depot')
    op.drop_column('enthusiasts', 'route_number')
    op.drop_column('enthusiasts', 'notes')
    op.drop_column('enthusiasts', 'spotted_at')
    op.drop_column('enthusiasts', 'photos_uploaded')

