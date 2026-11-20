"""合并双迁移分支head

Revision ID: f2f1bc37b495
Revises: b2c3d4e5f6a7, f5b92f785846
Create Date: 2026-07-31 16:23:52.742500

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2f1bc37b495'
down_revision: Union[str, None] = ('b2c3d4e5f6a7', 'f5b92f785846')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
