"""merge two heads

Revision ID: 814360d8de7d
Revises: b8e3f9c2a1d4, f43e2566ff01
Create Date: 2026-08-27 19:57:49.008699

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '814360d8de7d'
down_revision: Union[str, None] = ('b8e3f9c2a1d4', 'f43e2566ff01')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
