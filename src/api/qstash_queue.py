# STATUS: IMPLEMENTED BUT NOT WIRED
# This module is not imported by app.py. Async ingest currently runs via threading.Thread.
# Retained for future Upstash QStash integration.
# Do not assume this code is active or enforced at runtime.
"""QStash queue integration for asynchronous task processing."""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import requests

if TYPE_CHECKING:
    pass


@dataclass
class QueueMessage:
    """Queue message data structure."""
    topic: str
    payload: dict[str, Any]


class QStashQueue:
    """QStash queue client for publishing messages to async workers."""

    def __init__(self):
        """Initialize QStash client from environment variables."""
        self.url = os.getenv("QSTASH_URL")
        self.token = os.getenv("QSTASH_TOKEN")
        self.current_key = os.getenv("QSTASH_CURRENT_SIGNING_KEY")

        if not self.url or not self.token or not self.current_key:
            raise ValueError(
                "QSTASH_URL, QSTASH_TOKEN, and QSTASH_CURRENT_SIGNING_KEY must be set"
            )

        self.verify_endpoint = f"{self.url}/v2/verify"

    def _sign_payload(self, payload: str) -> str:
        """Generate HMAC signature for payload using current signing key.

        Args:
            payload: JSON string to sign

        Returns:
            str: Hexdigest of HMAC-SHA256 signature
        """
        signature = hmac.new(
            self.current_key.encode(),
            payload.encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(signature).decode()

    def enqueue(self, topic: str, payload: dict[str, Any]) -> str:
        """
        Publish a message to the specified QStash topic.

        Args:
            topic: Queue topic name
            payload: Message payload (dict)

        Returns:
            str: QStash message ID

        Raises:
            requests.HTTPError: If request fails
            ValueError: If response is invalid
        """
        message_body = json.dumps({"topic": topic, "payload": payload})
        signature = self._sign_payload(message_body)

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Upstash-Signature": f"v1,{signature}",
            "Content-Type": "application/json",
        }

        publish_endpoint = f"{self.url}/v2/publish/{topic}"

        try:
            response = requests.post(
                publish_endpoint,
                headers=headers,
                data=message_body,
                timeout=10,
            )
            response.raise_for_status()

            result = response.json()
            message_id = result.get("messageId")

            if not message_id:
                raise ValueError("QStash response missing messageId")

            return message_id

        except requests.RequestException as exc:
            # Log and re-raise
            print(f"QStash enqueue error: {exc}")
            raise

    def verify_webhook(self, headers: dict[str, str], body: str) -> bool:
        """
        Verify webhook signature from QStash.

        Args:
            headers: Request headers containing Upstash-Signature
            body: Raw request body

        Returns:
            bool: True if signature is valid
        """
        upstash_signature = headers.get("upstash-signature", "")

        if not upstash_signature:
            return False

        # Parse signature format: "v1,<signature>"
        try:
            version, provided_signature = upstash_signature.split(",", 1)
            if version != "v1":
                return False
        except (ValueError, IndexError):
            return False

        # Calculate expected signature
        expected_sig = self._sign_payload(body)

        # Compare signatures
        return hmac.compare_digest(expected_sig, provided_signature)
