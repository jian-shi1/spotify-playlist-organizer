from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .storage import OrganizerError, load, save


def track(row):
    # New development-mode responses use item; older apps can still return track.
    value = row.get("item", row.get("track"))
    if value is None:
        raise OrganizerError(
            "Unavailable/null playlist item; aborting rather than using incomplete data.")
    return value


def build_targets(rows, now=None, *, min_songs=3, min_solo_songs=0):
    if min_songs < 1 or min_solo_songs < 0:
        raise OrganizerError(
            "Minimum songs must be at least 1; minimum solo songs cannot be negative.")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=21)
    artists, names, recent = defaultdict(dict), {}, {}
    solo_songs = defaultdict(set)
    skipped = 0
    for row in rows:
        song = track(row)
        if row.get("is_local") or song.get("is_local") or song.get("type") != "track":
            skipped += 1
            continue
        sid, uri = song.get("id"), song.get("uri")
        if not sid or uri != f"spotify:track:{sid}" or not song.get("artists"):
            raise OrganizerError(
                "Incomplete track metadata in master playlist.")
        for artist in song["artists"]:
            aid = artist.get("id")
            if not aid or not artist.get("name"):
                raise OrganizerError("Incomplete credited artist metadata.")
            names[aid] = artist["name"]
            artists[aid][sid] = uri
            if len(song["artists"]) == 1:
                solo_songs[aid].add(sid)
        try:
            added = datetime.fromisoformat(
                row["added_at"].replace("Z", "+00:00"))
            if added.tzinfo is None:
                raise ValueError()
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise OrganizerError(
                "Missing/invalid added_at; cannot safely calculate recent playlist.") from exc
        if cutoff <= added <= now:
            if sid not in recent or recent[sid][0] < added:
                recent[sid] = (added, uri)
    targets = {
        f"artist:{aid}": {"name": f"(gen) Artist - {names[aid]}"[:100], "uris": list(songs.values())}
        for aid, songs in sorted(artists.items())
        if len(songs) >= min_songs and len(solo_songs[aid]) >= min_solo_songs
    }
    targets["recent"] = {"name": "Recently Added - 3 Weeks", "uris": [
        uri for _, uri in sorted(recent.values(), key=lambda pair: (pair[0], pair[1]), reverse=True)
    ]}
    return targets, skipped


def marker(master, key):
    return f"spotify-playlist-organizer:v1:{master}:{key}"


class Organizer:
    def __init__(self, api, master, state_path):
        self.api, self.master, self.path = api, master, state_path

    def plan(self, now=None, *, min_songs=3, min_solo_songs=0):
        profile = self.api.request("GET", "me")
        user = profile["id"]
        identity = profile.get("account_id") or user
        state = load(self.path, {
                     "version": 1, "user": identity, "master": self.master, "playlists": {}})
        if state.get("version") != 1 or state.get("user") != identity or state.get("master") != self.master:
            raise OrganizerError(
                "State belongs to a different user/source or unsupported version.")
        source, rows = self.api.read_playlist(self.master)
        targets, skipped = build_targets(
            rows, now, min_songs=min_songs, min_solo_songs=min_solo_songs)
        inventory = self.api.pages("me/playlists?limit=50&offset=0")
        prefix = marker(self.master, "")
        recovered = {}
        for playlist in inventory:
            description = playlist.get("description") or ""
            if playlist.get("owner", {}).get("id") == user and description.startswith(prefix):
                key = description[len(prefix):]
                if key != "recent" and not key.startswith("artist:"):
                    continue
                if key in recovered:
                    raise OrganizerError(
                        "Duplicate organizer markers found; resolve duplicate generated playlists first.")
                recovered[key] = playlist["id"]
        mappings = dict(state["playlists"])
        for key, pid in recovered.items():
            if key in mappings and mappings[key] != pid:
                raise OrganizerError(
                    "Stored playlist ID conflicts with recovered marker.")
            mappings[key] = pid
        # Previously managed artists below the threshold are emptied, never deleted.
        for key in mappings:
            if key not in targets:
                if not key.startswith("artist:"):
                    raise OrganizerError("Unexpected state key.")
                targets[key] = {"name": f"Inactive {key}", "uris": []}
        plan = []
        for key, target in targets.items():
            pid = mappings.get(key)
            current, snapshot = [], None
            if pid:
                if pid == self.master:
                    raise OrganizerError(
                        "State points to the master playlist; refusing writes.")
                metadata, target_rows = self.api.read_playlist(pid)
                if (metadata.get("owner", {}).get("id") != user or
                        metadata.get("description") != marker(self.master, key) or
                        metadata.get("public") is not False or metadata.get("collaborative")):
                    raise OrganizerError(
                        "Managed playlist ownership/marker/privacy changed; refusing writes.")
                snapshot = metadata["snapshot_id"]
                current = [track(row).get("uri") for row in target_rows]
                if any(not uri for uri in current):
                    raise OrganizerError(
                        "Cannot fully read generated playlist.")
                target["name"] = metadata["name"]
            desired = target["uris"]
            destructive = current != desired[:len(current)]
            plan.append(dict(key=key, id=pid, name=target["name"], before=current,
                             after=desired, snapshot=snapshot, destructive=destructive))
        return {"user": user, "source_snapshot": source["snapshot_id"], "state": state,
                "mappings": mappings, "changes": plan, "skipped": skipped, "source_count": len(rows)}

    def apply(self, plan, allow_destructive=False):
        changes = plan["changes"]
        if any(change["destructive"] for change in changes) and not allow_destructive:
            raise OrganizerError(
                "Removal/reordering requires explicit --allow-destructive approval; nothing written.")
        if self.api.request("GET", f"playlists/{self.master}").get("snapshot_id") != plan["source_snapshot"]:
            raise OrganizerError(
                "Master changed after planning; nothing written.")
        # Validate ALL targets again before the first mutation.
        for change in changes:
            if change["id"]:
                metadata = self.api.request("GET", f"playlists/{change['id']}")
                if (metadata.get("snapshot_id") != change["snapshot"] or
                    metadata.get("description") != marker(self.master, change["key"]) or
                    metadata.get("owner", {}).get("id") != plan["user"] or
                        metadata.get("public") is not False or metadata.get("collaborative")):
                    raise OrganizerError(
                        "Target changed after planning; nothing written.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        save(self.path.parent / "backups" / f"{stamp}.json", plan)
        state = plan["state"]
        state["playlists"] = dict(plan["mappings"])
        save(self.path, state)
        for change in changes:
            if change["id"] == self.master:
                raise OrganizerError("Refusing to modify master playlist.")
            if not change["id"]:
                created = self.api.request("POST", "me/playlists", json={
                    "name": change["name"], "public": False,
                    "description": marker(self.master, change["key"]),
                })
                change["id"] = created["id"]
                state["playlists"][change["key"]] = created["id"]
                # Persist immediately, before populating the playlist.
                save(self.path, state)
            pid = change["id"]
            if change["before"] == change["after"]:
                continue
            desired = change["after"]
            if change["destructive"]:
                self.api.request(
                    "PUT", f"playlists/{pid}/items", json={"uris": desired[:100]})
                remaining = desired[100:]
            else:
                remaining = desired[len(change["before"]):]
            for start in range(0, len(remaining), 100):
                self.api.request(
                    "POST", f"playlists/{pid}/items", json={"uris": remaining[start:start+100]})
            _, actual = self.api.read_playlist(pid)
            if [track(row).get("uri") for row in actual] != desired:
                raise OrganizerError(
                    "Post-write verification failed; stopped. Review backup and rerun dry-run.")
