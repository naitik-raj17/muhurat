"""phase 3: vendor contact email, status indexes for agent actions and approvals

Revision ID: a3c1f0e9b742
Revises: 7d920be5d81b
Create Date: 2026-10-16 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3c1f0e9b742'
down_revision: str | Sequence[str] | None = '7d920be5d81b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('contact_email', sa.Text(), nullable=True))
    op.create_index('ix_agent_actions_status', 'agent_actions', ['status'])
    op.create_index('ix_approval_requests_status', 'approval_requests', ['status'])


def downgrade() -> None:
    op.drop_index('ix_approval_requests_status', table_name='approval_requests')
    op.drop_index('ix_agent_actions_status', table_name='agent_actions')
    op.drop_column('vendors', 'contact_email')
