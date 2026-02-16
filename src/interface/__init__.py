"""Interface layer for Narada - handles user interactions and presentation concerns.

This layer contains:
- CLI interface (Typer + Rich) for command-line interactions
- Future: Web interface (FastAPI) for browser access

Following Clean Architecture principles:
- Only depends on Application layer (use cases)
- Never imports Infrastructure layer directly
- Contains no business logic - only presentation logic
"""
