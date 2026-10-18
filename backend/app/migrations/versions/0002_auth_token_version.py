import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_auth_token_version"
down_revision = "0001_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "token_version" not in columns:
        op.add_column(
            "users",
            sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "token_version" in columns:
        op.drop_column("users", "token_version")
