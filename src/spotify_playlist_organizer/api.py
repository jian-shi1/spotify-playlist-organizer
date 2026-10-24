import time
from urllib.parse import urlparse

import requests

from .storage import OrganizerError

BASE = "https://api.spotify.com/v1/"


class Spotify:
    def __init__(self, oauth, master, session=None, sleep=time.sleep):
        self.oauth, self.master = oauth, master
        self.session = session or requests.Session()
        self.sleep = sleep

    def request(self, method, path, **kwargs):
        url = path if path.startswith("https://") else BASE + path.lstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.spotify.com" or not parsed.path.startswith("/v1/"):
            raise OrganizerError("Refusing unexpected API pagination URL.")
        if method != "GET" and parsed.path.startswith(f"/v1/playlists/{self.master}"):
            raise OrganizerError("Refusing to modify the master playlist.")
        refreshed = False
        for attempt in range(6):
            try:
                response = self.session.request(method, url, timeout=30, allow_redirects=False,
                    headers={"Authorization": "Bearer " + self.oauth.access_token()}, **kwargs)
            except requests.RequestException as exc:
                # A timed-out write may have succeeded: never blindly retry it.
                if method == "GET" and attempt < 5:
                    self.sleep(2 ** attempt)
                    continue
                raise OrganizerError("Network failure; stopped. A write may have succeeded; rerun dry-run.") from exc
            if response.status_code == 401 and not refreshed:
                self.oauth.access_token(force=True)
                refreshed = True
                continue
            if response.status_code == 429:
                try:
                    quota = response.json().get("error", {}).get("reason") == "QUOTA_EXCEEDED"
                except ValueError:
                    quota = False
                if quota:
                    raise OrganizerError("Spotify developer-account quota exceeded; retry later.")
            if response.status_code == 429 and attempt < 5:
                try:
                    delay = max(1, float(response.headers.get("Retry-After", "5")))
                except ValueError:
                    delay = 5
                if delay > 300:
                    raise OrganizerError("Spotify rate limit exceeds 5 minutes; retry later.")
                self.sleep(delay)
                continue
            if 500 <= response.status_code < 600 and method == "GET" and attempt < 5:
                self.sleep(2 ** attempt)
                continue
            if not 200 <= response.status_code < 300:
                raise OrganizerError(f"Spotify HTTP {response.status_code} on {method} {parsed.path}; stopped.")
            if response.status_code == 204:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise OrganizerError("Invalid Spotify response; stopped.") from exc
        raise OrganizerError("Spotify retry limit reached; stopped.")

    def pages(self, path):
        items, seen, expected = [], set(), None
        while path:
            if path in seen:
                raise OrganizerError("Repeated pagination URL; source may be incomplete.")
            seen.add(path)
            page = self.request("GET", path)
            if not isinstance(page.get("items"), list) or not isinstance(page.get("total"), int):
                raise OrganizerError("Incomplete pagination response.")
            if expected is None:
                expected = page["total"]
            if page["total"] != expected or page.get("offset") != len(items):
                raise OrganizerError("Playlist changed or pagination is incomplete.")
            items.extend(page["items"])
            if "next" not in page:
                raise OrganizerError("Missing pagination continuation field.")
            path = page["next"]
        if len(items) != expected:
            raise OrganizerError("Playlist item count mismatch; no synchronization allowed.")
        return items

    def read_playlist(self, playlist_id):
        before = self.request("GET", f"playlists/{playlist_id}")
        if not before.get("snapshot_id"):
            raise OrganizerError("Missing playlist snapshot.")
        rows = self.pages(f"playlists/{playlist_id}/items?limit=50&offset=0")
        after = self.request("GET", f"playlists/{playlist_id}")
        if before["snapshot_id"] != after.get("snapshot_id"):
            raise OrganizerError("Playlist changed while being read; retry later.")
        return before, rows
