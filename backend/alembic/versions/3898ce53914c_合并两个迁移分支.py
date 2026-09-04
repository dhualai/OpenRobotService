"""合并两个迁移分支

Revision ID: 3898ce53914c
Revises: a7d1e2f3g4h5
Create Date: 2026-08-27 19:30:50.334183

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3898ce53914c'
down_revision: Union[str, None] = 'a7d1e2f3g4h5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
