"""Prepare a launchd job in the project. Does not install or enable it."""
import argparse
import plistlib
from pathlib import Path
from spotify_playlist_organizer.options import add_threshold_arguments

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="Prepare a job that writes to Spotify")
parser.add_argument("--allow-destructive", action="store_true", help="Include removal/reordering approval")
add_threshold_arguments(parser)
args = parser.parse_args()
if args.allow_destructive and not args.apply:
    parser.error("--allow-destructive requires --apply")
root = Path(__file__).resolve().parents[1]
command = [str(root / ".venv/bin/spotify-playlist-organizer"), "sync"]
command.append("--apply" if args.apply else "--dry-run")
command.extend(["--min-songs", str(args.min_songs), "--min-solo-songs", str(args.min_solo_songs)])
if args.allow_destructive:
    command.append("--allow-destructive")
log_dir = root / ".data/logs"
log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
job = {
    "Label": "local.spotify-playlist-organizer",
    "ProgramArguments": command,
    "WorkingDirectory": str(root),
    "StartCalendarInterval": {"Weekday": 0, "Hour": 10, "Minute": 0},
    "StandardOutPath": str(log_dir / "weekly.log"),
    "StandardErrorPath": str(log_dir / "weekly-error.log"),
    "ProcessType": "Background",
    "Umask": 0o077,
}
path = root / "schedule/local.spotify-playlist-organizer.plist"
path.write_bytes(plistlib.dumps(job))
print(f"Prepared {path}; Sunday 10:00 in the Mac's local timezone. Not installed or enabled.")
