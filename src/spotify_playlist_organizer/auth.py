import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .storage import OrganizerError, load, save

SCOPES = "playlist-read-private playlist-read-collaborative playlist-modify-private"


class OAuth:
    def __init__(self, client_id, secret, redirect, path):
        self.client_id, self.secret, self.redirect, self.path = client_id, secret, redirect, path
        self.token = load(path, {})

    def exchange(self, data):
        try:
            response = requests.post(
                "https://accounts.spotify.com/api/token", data=data,
                auth=(self.client_id, self.secret), timeout=30,
            )
            if response.status_code != 200:
                raise OrganizerError(f"OAuth failed (HTTP {response.status_code}); run auth again.")
            token = response.json()
            token.setdefault("refresh_token", self.token.get("refresh_token"))
            token["expires_at"] = time.time() + token["expires_in"]
            save(self.path, token)
            self.token = token
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise OrganizerError("OAuth request failed; check network and app credentials.") from exc

    def access_token(self, force=False):
        if not self.token.get("refresh_token"):
            raise OrganizerError("No authorization cached. Run spotify-playlist-organizer auth first.")
        if force or self.token.get("expires_at", 0) < time.time() + 60:
            self.exchange({"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]})
        return self.token["access_token"]

    def authorize(self):
        redirect = urlparse(self.redirect)
        if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or not redirect.port:
            raise OrganizerError("Use a loopback redirect such as http://127.0.0.1:8888/callback.")
        state = secrets.token_urlsafe(32)
        result = {}

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                valid = parsed.path == redirect.path and query.get("state") == [state]
                if valid:
                    result.update(query)
                self.send_response(200 if valid else 400)
                self.end_headers()
                self.wfile.write(b"Authorization received. Return to the terminal." if valid else b"Invalid callback.")

            def log_message(self, *_):
                pass  # Never log authorization codes or callback query strings.

        url = "https://accounts.spotify.com/authorize?" + urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect,
            "scope": SCOPES, "state": state,
        })
        with HTTPServer(("127.0.0.1", redirect.port), Callback) as server:
            server.timeout = 1
            print("Open this URL in your browser and approve access:\n" + url, flush=True)
            deadline = time.monotonic() + 300
            while not result and time.monotonic() < deadline:
                server.handle_request()
        if not result.get("code"):
            raise OrganizerError("Authorization declined or timed out. Run auth again.")
        self.exchange({"grant_type": "authorization_code", "code": result["code"][0],
                       "redirect_uri": self.redirect})
        print("Authorization saved locally with owner-only permissions.")
