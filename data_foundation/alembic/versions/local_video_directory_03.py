"""Local videos folder index with nullable recording metadata kept separate from scenic records."""
from alembic import op
revision='local_video_directory_03'
down_revision='local_project_flow_02'
branch_labels=None
depends_on=None
def upgrade():
    from data_foundation.ingestion.local_videos import table
    table.create(op.get_bind(),checkfirst=True)
def downgrade(): op.drop_table('local_video_files')
