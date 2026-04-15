"""QStash queue tests."""

import json

import pytest
from requests.exceptions import RequestException

from src.api.qstash_queue import QStashQueue, QueueMessage


class MockQStashResponse:
    """Mock QStash HTTP response."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data or {"messageId": "msg_test_123"}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RequestException(f"HTTP {self.status_code}")


class TestQStashQueue:
    """QStash queue unit tests."""

    def test_enqueue_success(self, monkeypatch):
        """Test successful message enqueue."""

        def mock_post(url, headers, data, timeout):
            return MockQStashResponse()

        monkeypatch.setattr("requests.post", mock_post)

        # Set required env vars
        monkeypatch.setenv("QSTASH_URL", "https://qstash.upstash.io")
        monkeypatch.setenv("QSTASH_TOKEN", "test_token")
        monkeypatch.setenv("QSTASH_CURRENT_SIGNING_KEY", "test_key")

        queue = QStashQueue()
        message_id = queue.enqueue("test-topic", {"user_id": "123"})

        assert message_id == "msg_test_123"

    def test_enqueue_http_error(self, monkeypatch):
        """Test enqueue with HTTP error."""

        def mock_post(url, headers, data, timeout):
            return MockQStashResponse(status_code=500)

        monkeypatch.setattr("requests.post", mock_post)
        monkeypatch.setenv("QSTASH_URL", "https://qstash.upstash.io")
        monkeypatch.setenv("QSTASH_TOKEN", "test_token")
        monkeypatch.setenv("QSTASH_CURRENT_SIGNING_KEY", "test_key")

        queue = QStashQueue()

        with pytest.raises(RequestException):
            queue.enqueue("test-topic", {"user_id": "123"})

    def test_verify_webhook_valid(self, monkeypatch):
        """Test webhook signature verification with valid signature."""

        monkeypatch.setenv("QSTASH_URL", "https://qstash.upstash.io")
        monkeypatch.setenv("QSTASH_TOKEN", "test_token")
        monkeypatch.setenv("QSTASH_CURRENT_SIGNING_KEY", "test_key")

        queue = QStashQueue()
        body = '{"topic": "test", "payload": {}}'
        signature = queue._sign_payload(body)

        headers = {"upstash-signature": f"v1,{signature}"}
        is_valid = queue.verify_webhook(headers, body)

        assert is_valid

    def test_verify_webhook_invalid_signature(self, monkeypatch):
        """Test webhook signature verification with invalid signature."""

        monkeypatch.setenv("QSTASH_URL", "https://qstash.upstash.io")
        monkeypatch.setenv("QSTASH_TOKEN", "test_token")
        monkeypatch.setenv("QSTASH_CURRENT_SIGNING_KEY", "test_key")

        queue = QStashQueue()
        body = '{"topic": "test", "payload": {}}'

        headers = {"upstash-signature": "v1,invalid_signature"}
        is_valid = queue.verify_webhook(headers, body)

        assert not is_valid

    def test_verify_webhook_missing_header(self, monkeypatch):
        """Test webhook verification with missing signature header."""

        monkeypatch.setenv("QSTASH_URL", "https://qstash.upstash.io")
        monkeypatch.setenv("QSTASH_TOKEN", "test_token")
        monkeypatch.setenv("QSTASH_CURRENT_SIGNING_KEY", "test_key")

        queue = QStashQueue()
        body = '{"topic": "test", "payload": {}}'
        headers = {}

        is_valid = queue.verify_webhook(headers, body)

        assert not is_valid
