"""One-time OAuth consent flow to get a refresh token for pushing events into
a personal Google Calendar. Uses the OAuth "Desktop app" loopback flow: opens
a browser for consent, catches the redirect on a local server, exchanges the
code for tokens, and prints the refresh token to store in .env.

Usage:
    python scripts/google_calendar_auth.py <client_id> <client_secret>
"""

import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

SCOPE = "https://www.googleapis.com/auth/calendar"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALLBACK_PORT = 8765


class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None

    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Login complete, you can close this tab.</body></html>")

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python scripts/google_calendar_auth.py <client_id> <client_secret>", file=sys.stderr)
        return 1
    client_id, client_secret = sys.argv[1], sys.argv[2]
    redirect_uri = f"http://localhost:{CALLBACK_PORT}"

    auth_url = (
        f"{AUTH_ENDPOINT}?client_id={client_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={SCOPE}&access_type=offline&prompt=consent"
    )
    print(f"Opening browser for consent:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    print(f"Waiting for redirect on {redirect_uri} ...")
    server.handle_request()

    code = _CallbackHandler.auth_code
    if not code:
        print("No authorization code received.", file=sys.stderr)
        return 1

    response = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    tokens = response.json()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"No refresh_token in response: {tokens}", file=sys.stderr)
        return 1

    print("\nSuccess. Add this to .env:\n")
    print(f"GOOGLE_CALENDAR_CLIENT_ID={client_id}")
    print(f"GOOGLE_CALENDAR_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_CALENDAR_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
