"""鍚堝苟涓変釜杩佺Щ鍒嗘敮

Revision ID: 38a88928cc6b
Revises: 2b6e9d1f3c40, b1d2e3f4a5c6, e5f8d2a9c1b3
Create Date: 2026-08-14 09:54:12.183816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38a88928cc6b'
down_revision: Union[str, None] = ('2b6e9d1f3c40', 'b1d2e3f4a5c6', 'e5f8d2a9c1b3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
