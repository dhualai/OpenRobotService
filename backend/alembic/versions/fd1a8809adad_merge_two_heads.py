"""merge two heads

Revision ID: fd1a8809adad
Revises: 2b7155c7fb55, 3898ce53914c
Create Date: 2026-08-27 19:44:08.063679

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd1a8809adad'
down_revision: Union[str, None] = ('2b7155c7fb55', '3898ce53914c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
