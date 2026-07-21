from __future__ import annotations

import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import parse_qs, urlparse

from config import AccountConfig, ClientCredentials
from api import HttpClient, normalize_instance_url


OAUTH_PORT = 8085
REDIRECT_URI = f"http://localhost:{OAUTH_PORT}/"
SCOPES = "read write follow"


def register_app(instance: str) -> ClientCredentials:
    client = HttpClient(instance)
    response = client.post(
        "/api/v1/apps",
        {
            "client_name": "BTMastodon",
            "redirect_uris": REDIRECT_URI,
            "scopes": SCOPES,
            "website": "https://example.invalid/btmastodon",
        },
        access_token="",
    )
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected app registration response")
    return ClientCredentials(
        client_id=str(response["client_id"]),
        client_secret=str(response["client_secret"]),
    )


def authorization_url(instance: str, client_id: str, state: str | None = None) -> str:
    base_url = normalize_instance_url(instance)
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    }
    if state:
        auth_params["state"] = state
    params = urlencode(auth_params)
    return f"{base_url}/oauth/authorize?{params}"


def exchange_code(instance: str, credentials: ClientCredentials, code: str) -> AccountConfig:
    client = HttpClient(instance)
    response = client.post(
        "/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "redirect_uri": REDIRECT_URI,
        },
        access_token="",
    )
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected token response")
    return AccountConfig(
        instance=normalize_instance_url(instance),
        access_token=str(response["access_token"]),
        client=credentials,
    )


class MastodonClient:
    def __init__(self, config: AccountConfig) -> None:
        self.http = HttpClient(config.instance, config.access_token)

    def verify_account(self) -> dict:
        response = self.http.get("/api/v1/accounts/verify_credentials")
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected account response")
        return response

    def account_following(self, account_id: str, limit: int = 80) -> list[dict]:
        response = self.http.get(
            f"/api/v1/accounts/{quote(account_id)}/following",
            {"limit": limit},
        )
        if not isinstance(response, list):
            raise RuntimeError("Unexpected following response")
        return response

    def home_timeline(self, limit: int = 20, max_id: str | None = None) -> list[dict]:
        params = {"limit": limit}
        if max_id:
            params["max_id"] = max_id

        response = self.http.get("/api/v1/timelines/home", params)
        if not isinstance(response, list):
            raise RuntimeError("Unexpected timeline response")
        return response

    def lists(self) -> list[dict]:
        response = self.http.get("/api/v1/lists")
        if not isinstance(response, list):
            raise RuntimeError("Unexpected lists response")
        return response

    def create_list(self, title: str, exclusive: bool = True) -> dict:
        response = self.http.post(
            "/api/v1/lists",
            {"title": title, "exclusive": str(exclusive).lower()},
        )
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected list response")
        return response

    def update_list(
        self,
        list_id: str,
        title: str,
        exclusive: bool,
        replies_policy: str | None = None,
    ) -> dict:
        form = {
            "title": title,
            "exclusive": str(exclusive).lower(),
        }
        if replies_policy:
            form["replies_policy"] = replies_policy

        response = self.http.put(f"/api/v1/lists/{quote(list_id)}", form)
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected list response")
        return response

    def list_timeline(
        self,
        list_id: str,
        limit: int = 20,
        max_id: str | None = None,
    ) -> list[dict]:
        params = {"limit": limit}
        if max_id:
            params["max_id"] = max_id

        response = self.http.get(f"/api/v1/timelines/list/{quote(list_id)}", params)
        if not isinstance(response, list):
            raise RuntimeError("Unexpected list timeline response")
        return response

    def list_accounts(
        self,
        list_id: str,
        limit: int = 80,
        max_id: str | None = None,
    ) -> list[dict]:
        params = {"limit": limit}
        if max_id:
            params["max_id"] = max_id

        response = self.http.get(
            f"/api/v1/lists/{quote(list_id)}/accounts",
            params,
        )
        if not isinstance(response, list):
            raise RuntimeError("Unexpected list accounts response")
        return response

    def add_account_to_list(self, list_id: str, account_id: str) -> None:
        response = self.http.post(
            f"/api/v1/lists/{quote(list_id)}/accounts",
            {"account_ids[]": account_id},
        )
        if response not in ({}, None):
            raise RuntimeError("Unexpected add list account response")

    def notifications(self, limit: int = 20) -> list[dict]:
        response = self.http.get("/api/v1/notifications", {"limit": limit})
        if not isinstance(response, list):
            raise RuntimeError("Unexpected notifications response")
        return response

    def conversations(self, limit: int = 20, max_id: str | None = None) -> list[dict]:
        params = {"limit": limit}
        if max_id:
            params["max_id"] = max_id

        response = self.http.get("/api/v1/conversations", params)
        if not isinstance(response, list):
            raise RuntimeError("Unexpected conversations response")
        return response

    def status_context(self, status_id: str) -> dict:
        response = self.http.get(f"/api/v1/statuses/{quote(status_id)}/context")
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected status context response")
        return response

    def status(self, status_id: str) -> dict:
        response = self.http.get(f"/api/v1/statuses/{quote(status_id)}")
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected status response")
        return response

    def resolve_status_url(self, url: str) -> dict | None:
        response = self.http.get(
            "/api/v2/search",
            {
                "q": url,
                "type": "statuses",
                "resolve": "true",
                "limit": 1,
            },
        )
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected search response")

        statuses = response.get("statuses")
        if not isinstance(statuses, list):
            return None
        for status in statuses:
            if isinstance(status, dict):
                return status
        return None

    def post_status(
        self,
        status: str,
        visibility: str = "public",
        in_reply_to_id: str | None = None,
        quoted_status_id: str | None = None,
    ) -> dict:
        form = {
            "status": status,
            "visibility": visibility,
        }
        if in_reply_to_id:
            form["in_reply_to_id"] = in_reply_to_id
        if quoted_status_id:
            form["quoted_status_id"] = quoted_status_id

        response = self.http.post("/api/v1/statuses", form)
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected status response")
        return response

    def boost_status(self, status_id: str) -> dict:
        response = self.http.post(f"/api/v1/statuses/{quote(status_id)}/reblog")
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected boost response")
        return response

    def account_relationship(self, account_id: str) -> dict:
        response = self.http.get(
            "/api/v1/accounts/relationships",
            {"id[]": account_id},
        )
        if (
            not isinstance(response, list)
            or not response
            or not isinstance(response[0], dict)
        ):
            raise RuntimeError("Unexpected account relationship response")
        return response[0]

    def follow_account(self, account_id: str) -> dict:
        response = self.http.post(f"/api/v1/accounts/{quote(account_id)}/follow")
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected follow response")
        return response

    def unfollow_account(self, account_id: str) -> dict:
        response = self.http.post(f"/api/v1/accounts/{quote(account_id)}/unfollow")
        if not isinstance(response, dict):
            raise RuntimeError("Unexpected unfollow response")
        return response


def authorize_in_browser(instance: str, credentials: ClientCredentials) -> AccountConfig:
    state = secrets.token_urlsafe(32)
    url = authorization_url(instance, credentials.client_id, state)
    code = receive_authorization_code(url, state)
    return exchange_code(instance, credentials, code)


def receive_authorization_code(auth_url: str, state: str) -> str:
    server = _OAuthCallbackServer(("localhost", OAUTH_PORT), _OAuthCallbackHandler)
    server.expected_state = state

    try:
        _open_auth_url(auth_url)
        while server.code is None and server.error is None:
            server.handle_request()
    finally:
        server.server_close()
        _return_to_terminal()

    if server.error:
        raise RuntimeError(server.error)
    if not server.code:
        raise RuntimeError("Mastodon login did not return an authorization code")
    return server.code


def _open_auth_url(auth_url: str) -> None:
    try:
        from BTSpeak import dialogs, terminal, web_search

        dialogs.stopActivityIndicator()
        dialogs.clearScreen()
        dialogs.show_message(
            "Desktop mode browser will now load for Mastodon login. "
            "Press Enter and wait a few moments."
        )
        terminal.switch_and_wait(terminal.TARGET_DESKTOP)
        web_search.open_url(auth_url)
    except ImportError:
        print("Open this URL and approve BTMastodon:")
        print(auth_url)


def _return_to_terminal() -> None:
    try:
        from BTSpeak import terminal
        from BTSpeak.terminal import TARGET_TERMINAL

        terminal.switch(TARGET_TERMINAL)
    except ImportError:
        return


class _OAuthCallbackServer(HTTPServer):
    expected_state: str
    code: str | None = None
    error: str | None = None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: _OAuthCallbackServer

    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        returned_state = _first(query, "state")

        if returned_state != self.server.expected_state:
            self.server.error = "Mastodon login returned an invalid state"
            self._send_page("Login failed. Please return to Blazie mode.")
            return

        error = _first(query, "error")
        if error:
            description = _first(query, "error_description") or error
            self.server.error = f"Mastodon login was canceled or denied: {description}"
            self._send_page("Login canceled or denied. Please return to Blazie mode.")
            return

        code = _first(query, "code")
        if not code:
            self.server.error = "Mastodon login callback did not include a code"
            self._send_page("Login failed. Please return to Blazie mode.")
            return

        self.server.code = code
        self._send_page("Login succeeded. Please return to Blazie mode.")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_page(self, message: str) -> None:
        payload = (
            "<!doctype html><html><head><title>BTMastodon Login</title></head>"
            f"<body><p>{message}</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]
