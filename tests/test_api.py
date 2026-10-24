from unittest.mock import Mock

import pytest
import requests
import responses

from spotify_playlist_organizer.api import BASE, Spotify
from spotify_playlist_organizer.auth import OAuth
from spotify_playlist_organizer.storage import OrganizerError


def client():
    oauth = Mock()
    oauth.access_token.return_value = "secret-test-token"
    return Spotify(oauth, "master", sleep=Mock())


@responses.activate
def test_pagination_over_1000():
    api = client()
    for offset in range(0, 1051, 50):
        remaining = min(50, 1051-offset)
        next_url = BASE + f"playlists/master/items?offset={offset+50}" if offset+50 < 1051 else None
        path = "playlists/master/items" if offset == 0 else f"playlists/master/items?offset={offset}"
        responses.get(BASE + path, json={"items": list(range(remaining)), "offset": offset, "total": 1051, "next": next_url})
    assert len(api.pages("playlists/master/items")) == 1051


@responses.activate
def test_rate_limit_and_401_refresh():
    api = client()
    responses.get(BASE + "me", status=429, headers={"Retry-After": "2"})
    responses.get(BASE + "me", status=401)
    responses.get(BASE + "me", json={"id": "u"})
    assert api.request("GET", "me")["id"] == "u"
    api.sleep.assert_called_once_with(2)
    assert any(call.kwargs.get("force") for call in api.oauth.access_token.call_args_list)


@responses.activate
def test_truncated_pagination_aborts():
    responses.get(BASE + "broken", json={"items": [], "offset": 0, "total": 100, "next": None})
    with pytest.raises(OrganizerError):
        client().pages("broken")


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_never_writes_master(method):
    with pytest.raises(OrganizerError, match="master"):
        client().request(method, "playlists/master/items")


def test_no_credentials_to_foreign_pagination():
    with pytest.raises(OrganizerError, match="unexpected"):
        client().request("GET", "https://evil.example/v1/items")


@responses.activate
def test_write_timeout_not_retried():
    responses.post(BASE + "me/playlists", body=requests.Timeout())
    with pytest.raises(OrganizerError):
        client().request("POST", "me/playlists", json={})
    assert len(responses.calls) == 1


@responses.activate
def test_oauth_refresh_persists_refresh_token(tmp_path):
    oauth = OAuth("id", "secret", "http://127.0.0.1:8888/callback", tmp_path / "token.json")
    oauth.token = {"refresh_token": "refresh", "expires_at": 0}
    responses.post("https://accounts.spotify.com/api/token", json={"access_token": "new", "expires_in": 3600})
    assert oauth.access_token() == "new"
    assert oauth.token["refresh_token"] == "refresh"
    assert (tmp_path / "token.json").stat().st_mode & 0o777 == 0o600


@responses.activate
def test_snapshot_change_during_read_aborts():
    responses.get(BASE + "playlists/master", json={"snapshot_id": "a"})
    responses.get(BASE + "playlists/master/items?limit=50&offset=0", json={"items": [], "total": 0, "offset": 0, "next": None})
    responses.get(BASE + "playlists/master", json={"snapshot_id": "b"})
    with pytest.raises(OrganizerError, match="changed"):
        client().read_playlist("master")
