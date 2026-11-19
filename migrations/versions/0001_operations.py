"""Record campaign creation attempts."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campaign_operations",
        sa.Column("alias", sa.String(100), primary_key=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.Integer()),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("campaign_operations")
