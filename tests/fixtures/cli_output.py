"""Helpers for asserting on Rich-rendered CLI output."""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(output: str) -> str:
    """Strip ANSI styling so assertions can match the text a user reads.

    Rich highlights option-like tokens, and it splits them: ``--at`` renders as
    ``ESC[1;36m-ESC[0mESC[1;36m-atESC[0m``, so a literal ``"--at" in output``
    is false whenever colour is on. That makes such assertions pass on a
    developer's machine and fail in CI, which is how it was found.

    Applies to negative assertions too — ``"Traceback" not in output`` would
    pass spuriously against styled output.
    """
    return _ANSI.sub("", output)
