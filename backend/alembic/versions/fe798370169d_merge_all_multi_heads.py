"""merge all multi heads

Revision ID: fe798370169d
Revises: 9f3b7c2a1d40, 389e011724d6, 2b6e9d1f3c40
Create Date: 2026-08-11 16:25:32.699101

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe798370169d'
down_revision: Union[str, None] = ('9f3b7c2a1d40', '389e011724d6', '2b6e9d1f3c40')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
