"""Shared pytest fixtures for iletcomfort_web tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def import_web_module():
    """Imports iletcomfort_web. Forces ImportError to surface in test, not collection."""
    import iletcomfort_web
    return iletcomfort_web


from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture
def test_config(tmp_path):
    import iletcomfort_web

    return iletcomfort_web.Config(
        iletcomfort_account="user@example.com",
        iletcomfort_password="pw",
        webui_password="secret",
        webui_secret_key="test-secret-key-very-long-1234567890",
        iletcomfort_api_base="https://eu.dollin.net",
    )


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def app(test_config, mock_client):
    import iletcomfort_web

    application = iletcomfort_web.create_app(test_config, mock_client)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
