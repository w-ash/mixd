"""Display helpers shared by the CLI and API presentation surfaces."""


def humanize_identifier(value: str) -> str:
    """Render a snake_case identifier as a title-cased label.

    ``import_lastfm_history`` -> ``Import Lastfm History``.
    """
    return value.replace("_", " ").title()
