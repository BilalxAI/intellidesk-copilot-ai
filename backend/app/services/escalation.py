"""
Escalation delivery to IT Support.

Supported delivery mechanisms:
- Webhook: send JSON payload to a configured endpoint (Power Automate / Logic App / internal service).
- Microsoft Graph (delegated user): post a message into a group chat or channel.

Note on delegated auth:
This uses the OAuth2 Resource Owner Password Credentials (ROPC) flow, which is often
restricted by tenant policy. If ROPC is blocked in your tenant, use the webhook
path or switch to a device-code flow + token cache (not implemented here).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EscalationResult:
    ok: bool
    external_id: Optional[str] = None
    error: Optional[str] = None


class EscalationClient:
    def __init__(self, webhook_url: str = "", webhook_api_key: str = "", timeout_seconds: int = 10):
        self.webhook_url = (webhook_url or "").strip()
        self.webhook_api_key = (webhook_api_key or "").strip()
        self.timeout_seconds = int(timeout_seconds)

    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send_webhook(self, payload: Dict[str, Any]) -> EscalationResult:
        """Send escalation to a webhook endpoint."""
        if not self.webhook_url:
            return EscalationResult(ok=False, error="Escalation webhook is not configured.")

        headers = {"Content-Type": "application/json"}
        if self.webhook_api_key:
            headers["X-Api-Key"] = self.webhook_api_key

        try:
            resp = requests.post(self.webhook_url, json=payload, headers=headers, timeout=self.timeout_seconds)
            if 200 <= resp.status_code < 300:
                external_id = None
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        external_id = str(data.get("id") or data.get("ticketId") or data.get("ticket_id") or "") or None
                except Exception:
                    external_id = None
                return EscalationResult(ok=True, external_id=external_id)

            return EscalationResult(ok=False, error=f"Webhook returned HTTP {resp.status_code}: {resp.text[:500]}")
        except Exception as exc:
            logger.error("Escalation webhook send failed: %s", exc)
            return EscalationResult(ok=False, error=str(exc))


class GraphEscalationClient:
    """Post escalation messages using Microsoft Graph as a delegated user."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        timeout_seconds: int = 10,
        chat_id: str = "",
        team_id: str = "",
        channel_id: str = "",
        scopes: str = "",
    ):
        self.tenant_id = (tenant_id or "").strip()
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.username = (username or "").strip()
        self.password = (password or "").strip()
        self.timeout_seconds = int(timeout_seconds)
        self.chat_id = (chat_id or "").strip()
        self.team_id = (team_id or "").strip()
        self.channel_id = (channel_id or "").strip()
        self.scopes = (scopes or "").strip()

        self._cached_token: Optional[str] = None
        self._cached_token_exp: float = 0.0

    def enabled(self) -> bool:
        if not (self.tenant_id and self.client_id and self.client_secret and self.username and self.password):
            return False
        if self.chat_id:
            return True
        return bool(self.team_id and self.channel_id)

    def _token_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

    def _get_access_token(self) -> Tuple[Optional[str], Optional[str]]:
        now = time.time()
        if self._cached_token and now < (self._cached_token_exp - 60):
            return self._cached_token, None

        if not self.enabled():
            return None, "Graph escalation is not configured."

        # ROPC requires the app registration to allow this flow and the tenant
        # to permit it; scope must list delegated Graph permissions.
        requested_scopes = self.scopes or "https://graph.microsoft.com/.default"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "scope": requested_scopes,
        }
        try:
            resp = requests.post(self._token_endpoint(), data=data, timeout=self.timeout_seconds)
            if not (200 <= resp.status_code < 300):
                return None, f"Token request failed HTTP {resp.status_code}: {resp.text[:500]}"
            payload = resp.json()
            token = payload.get("access_token")
            expires_in = int(payload.get("expires_in") or 0)
            if not token:
                return None, "Token response missing access_token."
            self._cached_token = str(token)
            self._cached_token_exp = now + max(expires_in, 0)
            return self._cached_token, None
        except Exception as exc:
            return None, str(exc)

    def _post(self, url: str, body: Dict[str, Any]) -> EscalationResult:
        token, err = self._get_access_token()
        if not token:
            return EscalationResult(ok=False, error=err or "Unable to obtain Graph token.")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=self.timeout_seconds)
            if 200 <= resp.status_code < 300:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                external_id = None
                if isinstance(data, dict):
                    external_id = str(data.get("id") or "") or None
                return EscalationResult(ok=True, external_id=external_id)
            return EscalationResult(ok=False, error=f"Graph returned HTTP {resp.status_code}: {resp.text[:500]}")
        except Exception as exc:
            return EscalationResult(ok=False, error=str(exc))

    def send_to_support(self, payload: Dict[str, Any]) -> EscalationResult:
        """Send escalation to configured chat or channel."""
        if not self.enabled():
            return EscalationResult(ok=False, error="Graph escalation is not configured.")

        # Prefer HTML when available (allows colored header in some clients),
        # but fall back to plain text if not provided.
        html = payload.get("html")
        text = payload.get("text")
        if isinstance(html, str) and html.strip():
            body = {"body": {"contentType": "html", "content": html.strip()}}
        else:
            if not isinstance(text, str) or not text.strip():
                # Provide a readable default message.
                user = payload.get("user_id") or payload.get("user") or "unknown user"
                conv = payload.get("conversation_id") or "unknown conversation"
                text = f"IT Support escalation requested by {user} (conversation {conv})."
            body = {"body": {"contentType": "text", "content": text.strip()}}

        if self.chat_id:
            url = f"https://graph.microsoft.com/v1.0/chats/{self.chat_id}/messages"
            return self._post(url, body)

        url = f"https://graph.microsoft.com/v1.0/teams/{self.team_id}/channels/{self.channel_id}/messages"
        return self._post(url, body)

