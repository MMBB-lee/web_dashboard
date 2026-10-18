"""Separate authorized project estimates from original observations."""
from alembic import op
revision='local_project_flow_02'
down_revision='local_public_data_01'
branch_labels=None
depends_on=None

def upgrade():
    from data_foundation.ingestion.project_flow import table
    table.create(op.get_bind(),checkfirst=True)

def downgrade():
    op.drop_table('project_flow_data')
