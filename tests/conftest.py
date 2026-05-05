"""Shared pytest fixtures for iletcomfort_web tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def import_web_module():
    """Imports iletcomfort_web. Forces ImportError to surface in test, not collection."""
    import iletcomfort_web  # noqa: F401
    return iletcomfort_web
