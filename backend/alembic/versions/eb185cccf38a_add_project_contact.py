"""add project_contact

Revision ID: eb185cccf38a
Revises: 1120f2c12ed6
Create Date: 2026-08-06 16:38:41.037305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb185cccf38a'
down_revision: Union[str, None] = '1120f2c12ed6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
