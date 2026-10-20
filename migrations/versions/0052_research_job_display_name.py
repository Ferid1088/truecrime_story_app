"""A discovery run ("Search", D2) can be renamed by the user: `display_name` on
`research_jobs`, nullable — null means "use the deterministic generated name"
(computed from `input_json`, never stored, so it never drifts).

Additive: one nullable column; no existing row changes."""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_jobs", sa.Column("display_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("research_jobs", "display_name")
