"""The tenant every test fixture writes under unless a test names its own.

The value is ``"default"`` on purpose: hundreds of existing assertions compare
against that literal, and the point of the constant is that fixtures name a
tenant explicitly rather than relying on an entity or column default (none
exists since v0.12.0.2).
"""

from typing import Final

TEST_USER_ID: Final = "default"
