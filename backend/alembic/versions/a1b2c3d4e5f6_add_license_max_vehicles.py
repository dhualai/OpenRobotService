"""add_license_max_vehicles

Revision ID: a1b2c3d4e5f6
Revises: d4a6e0f13b57
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd4a6e0f13b57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project_license', sa.Column('max_vehicles', sa.Integer(), nullable=True, comment='允许最大车数，为空表示不限制'))


def downgrade() -> None:
    op.drop_column('project_license', 'max_vehicles')
