"""
Tests for the errors ``Client.request()`` raises.

The client runs in API-key mode so construction makes no network call, and the
session's ``request`` is replaced so every test runs offline.
"""

import pytest
import requests

from irp_integration.client import Client
from irp_integration.exceptions import IRPAPIError


@pytest.fixture
def client(monkeypatch):
    """Return an API-key ``Client`` built from test environment variables."""
    monkeypatch.setenv('RISK_MODELER_BASE_URL', 'https://rm.example.invalid')
    monkeypatch.setenv('RISK_MODELER_RESOURCE_GROUP_ID', 'test-resource-group')
    monkeypatch.setenv('RISK_MODELER_API_KEY', 'test-key')
    return Client()


def test_http_error_carries_the_response_status(client, monkeypatch):
    response = requests.Response()
    response.status_code = 400
    response._content = b'{"message": "Invalid analysis name"}'
    monkeypatch.setattr(client.session, 'request', lambda **kwargs: response)

    with pytest.raises(IRPAPIError) as excinfo:
        client.request('POST', '/platform/riskdata/v1/analyses')

    assert excinfo.value.status_code == 400


def test_connection_error_has_no_status(client, monkeypatch):
    def refuse(**kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(client.session, 'request', refuse)

    with pytest.raises(IRPAPIError) as excinfo:
        client.request('GET', '/platform/riskdata/v1/analyses')

    assert excinfo.value.status_code is None
