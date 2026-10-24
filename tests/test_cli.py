import sys
from unittest.mock import Mock

import pytest

from spotify_playlist_organizer import cli


@pytest.mark.parametrize("mode", [[], ["--dry-run"], ["--apply"], ["--apply", "--allow-destructive"]])
def test_thresholds_reach_planner_in_every_mode(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test")
    monkeypatch.setenv("SPOTIFY_MASTER_PLAYLIST", "m" * 22)
    organizer = Mock()
    organizer.plan.return_value = {"source_count": 0, "skipped": 0, "changes": []}
    monkeypatch.setattr(cli, "Organizer", Mock(return_value=organizer))
    monkeypatch.setattr(sys, "argv", ["organizer", "sync", *mode, "--min-songs", "5", "--min-solo-songs", "2"])
    cli.main()
    organizer.plan.assert_called_once_with(min_songs=5, min_solo_songs=2)
    assert organizer.apply.called == ("--apply" in mode)


def test_default_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test")
    monkeypatch.setenv("SPOTIFY_MASTER_PLAYLIST", "m" * 22)
    organizer = Mock()
    organizer.plan.return_value = {"source_count": 0, "skipped": 0, "changes": []}
    monkeypatch.setattr(cli, "Organizer", Mock(return_value=organizer))
    monkeypatch.setattr(sys, "argv", ["organizer"])
    cli.main()
    organizer.plan.assert_called_once_with(min_songs=3, min_solo_songs=0)


@pytest.mark.parametrize("flag,value", [("--min-songs", "0"), ("--min-songs", "-1"),
    ("--min-solo-songs", "-1"), ("--min-solo-songs", "1.5"), ("--min-songs", "no")])
def test_invalid_thresholds_rejected_before_api(monkeypatch, flag, value):
    monkeypatch.setattr(sys, "argv", ["organizer", flag, value])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
