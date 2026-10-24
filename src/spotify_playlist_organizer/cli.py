import argparse
import fcntl
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .api import Spotify
from .auth import OAuth
from .organizer import Organizer
from .options import add_threshold_arguments
from .storage import OrganizerError

ROOT = Path(__file__).resolve().parents[2]


def playlist_id(value):
    if value.startswith("spotify:playlist:"):
        value = value.split(":")[-1]
    elif value.startswith("https://open.spotify.com/"):
        parts = urlparse(value).path.rstrip("/").split("/")
        if len(parts) < 3 or parts[-2] != "playlist":
            raise OrganizerError("Expected a Spotify playlist URL.")
        value = parts[-1]
    if not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise OrganizerError("SPOTIFY_MASTER_PLAYLIST must be a playlist URL, URI or 22-character ID.")
    return value


def main():
    parser = argparse.ArgumentParser(description="Organize Spotify playlists; dry-run by default.")
    parser.add_argument("command", choices=["auth", "sync"], nargs="?", default="sync")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    mode.add_argument("--apply", action="store_true", help="Create playlists and append missing tracks")
    parser.add_argument("--allow-destructive", action="store_true", help="Explicit approval to remove/reorder managed tracks")
    add_threshold_arguments(parser)
    args = parser.parse_args()
    if args.allow_destructive and not args.apply:
        parser.error("--allow-destructive requires --apply")
    os.umask(0o077)
    load_dotenv(ROOT / ".env", override=False)
    data = ROOT / ".data"
    data.mkdir(mode=0o700, exist_ok=True)
    try:
        with (data / "organizer.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OrganizerError("Another organizer process is running.")
            client, secret = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
            if not client or not secret:
                raise OrganizerError("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in local .env.")
            oauth = OAuth(client, secret, os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"), data / "token.json")
            if args.command == "auth":
                oauth.authorize()
                return
            master = playlist_id(os.getenv("SPOTIFY_MASTER_PLAYLIST", ""))
            organizer = Organizer(Spotify(oauth, master), master, data / "state.json")
            plan = organizer.plan(min_songs=args.min_songs, min_solo_songs=args.min_solo_songs)
            print(f"Artist eligibility: at least {args.min_songs} distinct songs, "
                  f"at least {args.min_solo_songs} solo songs.")
            print(f"Read {plan['source_count']} source items; skipped {plan['skipped']} local/non-track items.")
            for change in plan["changes"]:
                before, after = change["before"], change["after"]
                status = "create" if not change["id"] else "unchanged" if before == after else "update"
                print(f"{status}: {change['name']} [{change['key']}] | {len(before)} -> {len(after)} tracks | "
                      f"+{len(set(after)-set(before))} -{len(set(before)-set(after))} | "
                      f"removal/reorder approval: {change['destructive']}")
            if args.apply:
                organizer.apply(plan, args.allow_destructive)
                print("Synchronization complete and verified.")
            else:
                print("Dry-run complete. No Spotify playlists or state mappings changed.")
    except (OrganizerError, OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, OrganizerError):
            parser.exit(1, f"Error: {exc}\n")
        parser.exit(1, "Error: unexpected data or local filesystem failure; stopped without further writes.\n")
