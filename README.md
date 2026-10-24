# spotify-playlist-organizer

(30 minutes of codex-plus work)

Organize a master Spotify playlist into private artist playlists and `Recently Added - 3 Weeks`.

## Run

Use Python 3.11+ (the local `.venv` uses 3.13):

```zsh
source .venv/bin/activate
# Install dependencies when setting up a fresh checkout:
python -m pip install -r requirements.txt
spotify-playlist-organizer auth
spotify-playlist-organizer sync --dry-run
```

Export `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_MASTER_PLAYLIST` (URL, URI or ID), and `SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback` in your terminal environment. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) with that exact redirect URI. Open the URL printed by `auth` and approve access. A local `.env` is optional; exported environment variables take precedence. Never commit credentials or `.data`, which contains refresh tokens and playlist state.

## Artist thresholds

```zsh
spotify-playlist-organizer sync --dry-run --min-songs 5 --min-solo-songs 2
```

- `--min-songs`: minimum number of distinct Spotify track IDs credited to the artist, including collaborations. Default `3`; must be at least `1`.
- `--min-solo-songs`: minimum number of distinct tracks whose Spotify artist list contains exactly one artist. Default `0` means no solo requirement; must be nonnegative.

Both conditions must pass. Qualifying artist playlists include **all** matching master tracks, including collaborations. Repeated copies of a track count once. Solo status follows Spotify artist credits, not song titles. Recently Added is unaffected by these thresholds and uses the most recent `added_at` for duplicate tracks within the last 21 days, newest first.

Flags work in default preview mode, explicit `--dry-run`, and `--apply`. They apply to the current invocation; repeat them on later runs to use the same settings.

## Applying changes

Preview first. `sync --apply` creates missing generated playlists and appends songs when the existing order permits it. Removal/reordering requires explicit `--apply --allow-destructive`; no destructive synchronization or schedule is enabled automatically.

Existing generated artist playlists that no longer meet the thresholds are planned to be emptied, never deleted. This includes raising either minimum. Emptying requires destructive approval. Names alone never identify generated playlists: their saved IDs and exact description markers do. Manual playlists are preserved. The master playlist is never written to.

All source and existing targets must be fully read before writing. Pagination, counts and snapshots are checked. Local files and non-track items are skipped; null/unavailable items or missing metadata cause an abort. Before writes, backups of the plan and previous track lists are stored in `.data/backups`. IDs are saved immediately after creation. Do not edit generated playlist markers. Spotify does not offer transactions across multiple playlists: a write failure can leave partial changes; the run stops and a new dry-run should be reviewed before retrying. Avoid editing playlists during synchronization.

## Weekly execution on macOS

```zsh
python scripts/prepare_schedule.py --min-songs 5 --min-solo-songs 2
```

This prepares `schedule/local.spotify-playlist-organizer.plist` for Sundays at 10:00 in the Mac's local timezone. It does **not** install or enable the job. It defaults to dry-run; `--apply` and `--allow-destructive` can be included only when deliberately preparing a writing job. Authorization is required before enabling scheduled changes.

Launchd does not read `~/.zshrc`; terminal exports alone will not reach it. Arrange credentials in the scheduled process's environment before enabling it. No credentials are embedded in the generated plist. The job requires a logged-in user session; sleep may delay execution. Logs are in `.data/logs`.

## Tests and troubleshooting

```zsh
python -m pytest -q
```

- Missing variables: export them in the same terminal where you run the command.
- OAuth failures: verify app credentials and exact redirect URI; run `auth` again. Port 8888 must be free.
- HTTP 403: check app authorization, scopes, Premium eligibility, and ownership/collaboration access to the source.
- HTTP 429: rate limits honor Retry-After with bounded retries; developer quota exhaustion aborts for a later retry.
- Incomplete source or changed snapshot: no writes start; retry after the source stops changing.
- Managed playlist missing/marker changed: restore access or the exact marker; the organizer refuses to overwrite another playlist.

Uses the 2026 `/playlists/{id}/items` and `POST /me/playlists` endpoints and accepts both `item` and legacy `track` response fields. See [Spotify's migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide). Folder placement is unsupported by the official API and is not automated.
