"""merge two heads

Revision ID: 87e115802055
Revises: 65386a53a7fd, c9d8e7f6a5b4
Create Date: 2026-08-17 13:14:13.863733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '87e115802055'
down_revision: Union[str, None] = ('65386a53a7fd', 'c9d8e7f6a5b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
