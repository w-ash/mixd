"""rename_connector_track_id_to_identifier

Rename connector_track_id -> connector_track_identifier and 
connector_playlist_id -> connector_playlist_identifier for clarity.

Revision ID: be28d934ebfd
Revises: e68ef4e64972
Create Date: 2025-08-17 00:44:33.094193

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'be28d934ebfd'
down_revision: Union[str, None] = 'e68ef4e64972'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename connector ID columns and fix missing FK relationship."""
    
    # 1. Check if tables and columns exist before operating
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # 2. Rename connector_track_id to connector_track_identifier in connector_tracks table
    # (this stores external service track IDs like Spotify track IDs)
    connector_tracks_cols = {col['name'] for col in inspector.get_columns('connector_tracks')}
    if 'connector_track_id' in connector_tracks_cols:
        op.alter_column('connector_tracks', 'connector_track_id', 
                       new_column_name='connector_track_identifier')
    
    # NOTE: track_mappings.connector_track_id stays unchanged - it's a FK to connector_tracks.id
    
    # 3. Rename connector_playlist_id to connector_playlist_identifier in connector_playlists table  
    connector_playlists_cols = {col['name'] for col in inspector.get_columns('connector_playlists')}
    if 'connector_playlist_id' in connector_playlists_cols:
        op.alter_column('connector_playlists', 'connector_playlist_id',
                       new_column_name='connector_playlist_identifier')
    
    # 4. Fix playlist_mappings: make connector_playlist_id a proper FK to connector_playlists.id
    # Check current playlist_mappings structure
    playlist_mappings_cols = {col['name']: col for col in inspector.get_columns('playlist_mappings')}
    
    if 'connector_playlist_id' in playlist_mappings_cols:
        current_col = playlist_mappings_cols['connector_playlist_id']
        
        # If it's currently VARCHAR (external ID), convert to FK using table recreation
        if 'VARCHAR' in str(current_col['type']).upper():
            # Since table is empty, just recreate it with correct structure
            op.execute("DROP TABLE playlist_mappings")
            
            # Recreate with correct schema
            op.execute("""
                CREATE TABLE playlist_mappings (
                    id INTEGER PRIMARY KEY,
                    playlist_id INTEGER NOT NULL,
                    connector_name VARCHAR(32) NOT NULL,
                    connector_playlist_id INTEGER NOT NULL,
                    last_synced DATETIME NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                    FOREIGN KEY (connector_playlist_id) REFERENCES connector_playlists(id) ON DELETE CASCADE,
                    CONSTRAINT uq_playlist_connector UNIQUE (playlist_id, connector_name),
                    CONSTRAINT uq_connector_playlist UNIQUE (connector_playlist_id)
                )
            """)


def downgrade() -> None:
    """Revert connector ID column renames and FK changes."""
    
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # Revert connector_track_identifier back to connector_track_id
    connector_tracks_cols = {col['name'] for col in inspector.get_columns('connector_tracks')}
    if 'connector_track_identifier' in connector_tracks_cols:
        op.alter_column('connector_tracks', 'connector_track_identifier',
                       new_column_name='connector_track_id')
    
    # Revert connector_playlist_identifier back to connector_playlist_id
    connector_playlists_cols = {col['name'] for col in inspector.get_columns('connector_playlists')}
    if 'connector_playlist_identifier' in connector_playlists_cols:
        op.alter_column('connector_playlists', 'connector_playlist_identifier', 
                       new_column_name='connector_playlist_id')
    
    # Revert playlist_mappings FK change
    playlist_mappings_cols = {col['name']: col for col in inspector.get_columns('playlist_mappings')}
    if 'connector_playlist_id' in playlist_mappings_cols:
        current_col = playlist_mappings_cols['connector_playlist_id']
        
        # If it's currently INTEGER (FK), convert back to VARCHAR
        if 'INTEGER' in str(current_col['type']).upper():
            op.drop_column('playlist_mappings', 'connector_playlist_id')
            op.add_column('playlist_mappings', 
                          sa.Column('connector_playlist_id', sa.String(64), nullable=False))
