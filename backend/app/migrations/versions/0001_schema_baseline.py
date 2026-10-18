from alembic import op

from app.db.session import Base
import app.db.models  # noqa: F401

revision = "0001_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    pass
