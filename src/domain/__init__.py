"""Narada domain layer - pure business logic with zero external dependencies."""

# Ensure submodules are importable as src.domain.entities, etc.
from . import entities as entities, matching as matching, transforms as transforms
