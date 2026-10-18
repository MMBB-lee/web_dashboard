"""Directory video P2PNet analyses and relative-time points."""
from alembic import op
revision='local_video_counts_04'
down_revision='local_video_directory_03'
branch_labels=None
depends_on=None
def upgrade():
    from data_foundation.ingestion.local_video_counts import analyses,points
    analyses.create(op.get_bind(),checkfirst=True);points.create(op.get_bind(),checkfirst=True)
def downgrade():
    op.drop_table('local_video_count_points');op.drop_table('local_video_analyses')
