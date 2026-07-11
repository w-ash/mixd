"""User-data marking — the chat prompt-injection defense convention.

Music metadata is attacker-controllable text: a track title, playlist name, or
tag can contain instructions. Free-text values that originate from the user's
library must reach the model labeled as data, never as instructions. The
convention has one marker and three boundaries:

- **Marker** (v0.9.1): dispatchers mark user-originated strings with a
  ``UserData`` ``str`` subclass carrying the raw value.
- **Model boundary (wrap)**: serializing a tool result into model context runs
  ``wrap_for_model``, enclosing marked values in ``<user_data>`` tags.
- **Frontend boundary (strip)**: tool-result event summaries pass through
  :func:`strip_user_data` before the SSE stream, so the frontend renders raw
  values and needs no strip sites of its own.
- **Input boundary (sanitize)**: ``registry.execute_tool`` applies
  :func:`strip_user_data` to every incoming tool_input, so wrapped values the
  model echoes back can never break lookups or persist.

v0.9.0 ships the strip boundary (the input sanitizer) and the :func:`wrap`
primitive; the ``UserData`` marker and ``wrap_for_model`` land in v0.9.1 with
the first tools that return user-library text. The tag literals live ONLY in
this module (plus the prompt text that teaches the model the convention).
"""

import re
from typing import cast

_TAG_RE = re.compile(r"</?user_data>")


def wrap(value: str) -> str:
    """Enclose a value in ``<user_data>`` tags for model-facing text.

    Any tag literals embedded in the value are removed first, so a value like
    ``X</user_data>IGNORE...`` cannot break out of its wrapper.
    """
    return f"<user_data>{_TAG_RE.sub('', value)}</user_data>"


def strip_user_data(obj: object) -> object:
    """Recursively remove all ``<user_data>`` tag literals from strings.

    Applied to outgoing event summaries (frontend boundary) and incoming tool
    inputs (input sanitizer). Rebuilds containers, including dict keys.
    """
    if isinstance(obj, str):
        return _TAG_RE.sub("", obj)
    if isinstance(obj, dict):
        items = cast("dict[object, object]", obj)
        return {strip_user_data(k): strip_user_data(v) for k, v in items.items()}
    if isinstance(obj, list):
        return [strip_user_data(v) for v in cast("list[object]", obj)]
    if isinstance(obj, tuple):
        return tuple(strip_user_data(v) for v in cast("tuple[object, ...]", obj))
    return obj
