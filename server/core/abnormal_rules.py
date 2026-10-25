"""Backward-compatible import path for the calculation engine.

New code should import from ``server.calculations.engine``. This facade remains
until downstream callers and test fixtures have migrated.
"""

from server.calculations.engine import *  # noqa: F401,F403
