"""Interface layer for Narada - handles user interactions and presentation concerns.

This layer contains:
- CLI interface for command-line interactions
- Shared abstractions for CLI and future web interface
- User interface concerns like result formatting and error handling

Following Clean Architecture principles:
- Only depends on Application layer (use cases)
- Never imports Infrastructure layer directly
- Contains no business logic - only presentation logic
"""

from __future__ import annotations
