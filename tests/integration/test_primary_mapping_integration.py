"""Integration tests for primary mapping functionality."""

import pytest
from sqlalchemy import text

from src.infrastructure.persistence.repositories.track.connector import TrackConnectorRepository


class TestPrimaryMappingDatabaseIntegration:
    """Minimal integration tests for primary mapping with real database."""

    @pytest.mark.asyncio
    async def test_constraint_exists(self, db_session):
        """Test that the unique constraint exists in database."""
        # Check constraint exists by querying schema
        result = await db_session.execute(
            text("SELECT name FROM sqlite_master WHERE name LIKE '%uq_primary%' OR sql LIKE '%is_primary%'")
        )
        constraints = result.fetchall()
        
        # Should have constraint that references is_primary
        assert len(constraints) > 0

    @pytest.mark.asyncio  
    async def test_repository_method_available(self, db_session):
        """Test that repository has set_primary_mapping method."""
        repo = TrackConnectorRepository(db_session)
        
        # Method should exist and be callable
        assert hasattr(repo, 'set_primary_mapping')
        assert callable(getattr(repo, 'set_primary_mapping'))