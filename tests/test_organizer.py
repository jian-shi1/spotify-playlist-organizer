from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from spotify_playlist_organizer.organizer import Organizer, build_targets, marker
from spotify_playlist_organizer.storage import OrganizerError, save

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
MASTER = "m" * 22


def row(sid="a", days=0, artists=("one",), field="item"):
    return {"added_at": (NOW - timedelta(days=days)).isoformat(), field: {
        "type": "track", "id": sid, "uri": f"spotify:track:{sid}",
        "artists": [{"id": aid, "name": aid} for aid in artists]}}


def test_collaborations_distinct_ids_recent_and_duplicates():
    rows = [row("a", 22, ("one", "two")), row("a", 2, ("one", "two")),
            row("b", 1, ("one", "two")), row("c", 21, ("one", "two")), row("d", 30, ("three",))]
    targets, skipped = build_targets(rows, NOW)
    assert skipped == 0
    assert targets["artist:one"]["uris"] == targets["artist:two"]["uris"] == [f"spotify:track:{x}" for x in "abc"]
    assert "artist:three" not in targets
    assert targets["recent"]["uris"] == [f"spotify:track:{x}" for x in "bac"]


def test_artist_ids_not_names_and_legacy_shape():
    rows = [row(x, artists=("one",), field="track") for x in "abc"]
    rows.append(row("d", artists=("different",)))
    for item in rows:
        item.get("item", item.get("track"))["artists"][0]["name"] = "same name"
    targets, _ = build_targets(rows, NOW)
    assert "artist:one" in targets and "artist:different" not in targets


@pytest.mark.parametrize("bad", [{"item": None}, {"item": {"type": "track", "id": "a"}}, row() | {"added_at": None}])
def test_incomplete_source_aborts(bad):
    with pytest.raises(OrganizerError):
        build_targets([bad], NOW)


def test_local_and_episode_skipped():
    local = row()
    local["item"]["is_local"] = True
    assert build_targets([local, {"item": {"type": "episode"}}], NOW)[1] == 2


def fake_api():
    api = Mock()
    api.request.side_effect = lambda method, path, **kw: {"id": "user"} if path == "me" else {"snapshot_id": "s"}
    api.read_playlist.return_value = ({"snapshot_id": "s"}, [row()])
    api.pages.return_value = []
    return api


def test_dry_run_no_writes(tmp_path):
    api = fake_api()
    organizer = Organizer(api, MASTER, tmp_path / "state.json")
    plan = organizer.plan(NOW)
    assert len(plan["changes"]) == 1
    assert all(call.args[0] == "GET" for call in api.request.call_args_list)
    assert not organizer.path.exists()


def test_source_failure_prevents_all_writes(tmp_path):
    api = fake_api()
    api.read_playlist.side_effect = OrganizerError("partial read")
    with pytest.raises(OrganizerError):
        Organizer(api, MASTER, tmp_path / "state.json").plan(NOW)
    assert all(call.args[0] == "GET" for call in api.request.call_args_list)


def managed_metadata(key="recent"):
    return {"id": "generated", "name": "Recently Added - 3 Weeks", "snapshot_id": "g",
            "owner": {"id": "user"}, "public": False, "collaborative": False,
            "description": marker(MASTER, key)}


def test_recovers_marked_playlist_preserves_manual_and_requires_approval(tmp_path):
    api = fake_api()
    managed = managed_metadata()
    manual = dict(managed, id="manual", description="My playlist")
    api.pages.return_value = [manual, managed]
    api.read_playlist.side_effect = lambda pid: ({"snapshot_id": "s"}, [row("new")]) if pid == MASTER else (managed, [row("old")])
    organizer = Organizer(api, MASTER, tmp_path / "state.json")
    plan = organizer.plan(NOW)
    assert plan["changes"][0]["id"] == "generated"
    with pytest.raises(OrganizerError, match="approval"):
        organizer.apply(plan)
    assert all(call.args[0] == "GET" for call in api.request.call_args_list)


def test_refuses_state_pointing_at_master(tmp_path):
    api = fake_api()
    save(tmp_path / "state.json", {"version": 1, "user": "user", "master": MASTER, "playlists": {"recent": MASTER}})
    with pytest.raises(OrganizerError, match="master"):
        Organizer(api, MASTER, tmp_path / "state.json").plan(NOW)


def test_ownership_marker_change_aborts(tmp_path):
    api = fake_api()
    save(tmp_path / "state.json", {"version": 1, "user": "user", "master": MASTER, "playlists": {"recent": "generated"}})
    api.read_playlist.side_effect = [({"snapshot_id": "s"}, [row()]), (dict(managed_metadata(), description="changed"), [])]
    with pytest.raises(OrganizerError, match="marker"):
        Organizer(api, MASTER, tmp_path / "state.json").plan(NOW)


def test_apply_create_persist_batch_and_verify(tmp_path):
    api = fake_api()
    desired = [f"spotify:track:{i}" for i in range(205)]
    def request(method, path, **kw):
        if method == "POST" and path == "me/playlists":
            return {"id": "new"}
        return {"snapshot_id": "s"}
    api.request.side_effect = request
    api.read_playlist.return_value = ({"snapshot_id": "g"}, [{"item": {"uri": uri}} for uri in desired])
    plan = {"user": "user", "source_snapshot": "s", "mappings": {},
            "state": {"playlists": {}}, "changes": [{"key": "recent", "id": None, "name": "Recent",
            "before": [], "after": desired, "snapshot": None, "destructive": False}]}
    Organizer(api, MASTER, tmp_path / "state.json").apply(plan)
    batches = [call.kwargs["json"]["uris"] for call in api.request.call_args_list if call.args[1] == "playlists/new/items"]
    assert list(map(len, batches)) == [100, 100, 5]
    assert '"new"' in (tmp_path / "state.json").read_text()
    assert list((tmp_path / "backups").glob("*.json"))


def test_source_snapshot_change_aborts_apply(tmp_path):
    api = fake_api()
    organizer = Organizer(api, MASTER, tmp_path / "state.json")
    plan = organizer.plan(NOW)
    api.request.return_value = {"snapshot_id": "changed"}
    api.request.side_effect = None
    with pytest.raises(OrganizerError, match="Master changed"):
        organizer.apply(plan, True)
    assert all(call.args[0] == "GET" for call in api.request.call_args_list)


def test_configurable_total_and_solo_thresholds():
    rows = [row("solo"), row("solo"), row("duet", artists=("one", "two")),
            row("duet2", artists=("one", "two"))]
    targets, _ = build_targets(rows, NOW, min_songs=3, min_solo_songs=1)
    assert targets["artist:one"]["uris"] == ["spotify:track:solo", "spotify:track:duet", "spotify:track:duet2"]
    assert "artist:two" not in targets
    # Repeated occurrences of the same solo song cannot satisfy a two-song minimum.
    targets, _ = build_targets(rows, NOW, min_songs=3, min_solo_songs=2)
    assert set(targets) == {"recent"}
    assert targets["recent"]["uris"] == build_targets(rows, NOW)[0]["recent"]["uris"]
    assert "artist:one" not in build_targets(rows, NOW, min_songs=4)[0]
    assert "artist:two" in build_targets(rows, NOW, min_songs=2)[0]


def test_solo_threshold_can_exceed_total_minimum():
    rows = [row(x) for x in "abc"]
    assert "artist:one" not in build_targets(rows, NOW, min_songs=1, min_solo_songs=4)[0]
    assert "artist:one" in build_targets(rows, NOW, min_songs=1, min_solo_songs=3)[0]


def test_new_threshold_reuses_existing_playlist_and_requires_approval_to_empty(tmp_path):
    api = fake_api()
    metadata = managed_metadata("artist:one")
    api.pages.return_value = [metadata]
    api.read_playlist.side_effect = lambda pid: ({"snapshot_id": "s"}, [row(x) for x in "abc"]) if pid == MASTER else (metadata, [row(x) for x in "abc"])
    organizer = Organizer(api, MASTER, tmp_path / "state.json")
    plan = organizer.plan(NOW, min_songs=4)
    artist = next(change for change in plan["changes"] if change["key"] == "artist:one")
    assert artist["id"] == "generated"
    assert artist["after"] == []
    with pytest.raises(OrganizerError, match="approval"):
        organizer.apply(plan)
