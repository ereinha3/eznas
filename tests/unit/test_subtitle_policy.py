"""Tests for the configurable subtitle + output-container policy.

All ffprobe output here is fabricated; nothing touches real media files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from orchestrator.models import MediaPolicyEntry, QualityConfig, StackConfig
from orchestrator.pipeline.remux import (
    RemuxPolicyError,
    SubtitlePolicy,
    TrackSelection,
    _AudioTrack,
    _SubtitleCandidate,
    build_crossmux_command,
    build_ffmpeg_command,
    build_remux,
    build_video_upgrade_command,
    select_subtitle_streams,
)
from orchestrator.pipeline.sweep import LibrarySweeper, unwanted_subtitle_streams
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo


# ---------------------------------------------------------------------------
# Fabricated ffprobe streams
# ---------------------------------------------------------------------------

def _video(index: int = 0) -> dict:
    return {"index": index, "codec_type": "video", "codec_name": "hevc",
            "disposition": {"default": 1}}


def _audio(index: int, lang: str = "eng", codec: str = "truehd") -> dict:
    return {"index": index, "codec_type": "audio", "codec_name": codec,
            "channels": 8, "tags": {"language": lang},
            "disposition": {"default": 1}}


def _sub(
    index: int,
    lang: str,
    codec: str = "subrip",
    *,
    title: Optional[str] = None,
    forced: bool = False,
    hi: bool = False,
    comment: bool = False,
    default: bool = False,
) -> dict:
    tags = {"language": lang}
    if title is not None:
        tags["title"] = title
    return {
        "index": index,
        "codec_type": "subtitle",
        "codec_name": codec,
        "tags": tags,
        "disposition": {
            "default": int(default),
            "forced": int(forced),
            "hearing_impaired": int(hi),
            "comment": int(comment),
        },
    }


PGS = "hdmv_pgs_subtitle"


def pirates_streams() -> List[dict]:
    """Model of Pirates of the Caribbean (2017): 44 subtitle tracks.

    idx 2-12: the 11 FGT release subtitle tracks (2 eng SRT + 9 PGS)
    idx 13: Bazarr eng SRT, idx 14-45: 32 identical untitled eng SRTs.
    """
    streams = [_video(0), _audio(1, "eng", "truehd")]
    streams.append(_sub(2, "eng", "subrip"))
    streams.append(_sub(3, "eng", "subrip", title="SDH"))
    for i, lang in enumerate(["eng", "fre", "fre", "spa", "spa", "chi", "chi", "jpn", "jpn"]):
        streams.append(_sub(4 + i, lang, PGS))
    streams.append(_sub(13, "eng", "subrip", title="EN (Bazarr)"))
    for idx in range(14, 46):
        streams.append(_sub(idx, "eng", "subrip"))
    assert sum(1 for s in streams if s["codec_type"] == "subtitle") == 44
    return streams


def _sel(
    subs=("eng",),
    *,
    container: str = "mkv",
    **policy,
) -> TrackSelection:
    return TrackSelection(
        audio=["eng"],
        subtitles=list(subs),
        subtitle_policy=SubtitlePolicy(**policy),
        container=container,
    )


def _picked(streams, selection, container: Optional[str] = None) -> List[int]:
    result = select_subtitle_streams(
        streams, selection, container=container or selection.container,
    )
    return [int(t.stream_index) for t in result.selected]


def _probe_ok(streams: List[dict]) -> MagicMock:
    return MagicMock(returncode=0, stdout=json.dumps({"streams": streams}), stderr="")


def _sub_maps(cmd: List[str], streams: List[dict], input_idx: int = 0) -> List[int]:
    """Return the subtitle stream indices mapped from ``input_idx``."""
    sub_indices = {s["index"] for s in streams if s["codec_type"] == "subtitle"}
    out = []
    for i, arg in enumerate(cmd[:-1]):
        if arg != "-map":
            continue
        target = cmd[i + 1]
        prefix, _, rest = target.partition(":")
        if prefix == str(input_idx) and rest.isdigit() and int(rest) in sub_indices:
            out.append(int(rest))
    return out


# ---------------------------------------------------------------------------
# Defaults / config
# ---------------------------------------------------------------------------

class TestPolicyConfig:
    def test_defaults_reproduce_current_policy(self):
        entry = MediaPolicyEntry()
        assert entry.keep_subs == ["eng"]
        assert entry.subtitles.max_per_language == 1
        assert entry.subtitles.prefer == ["text", "image"]
        assert entry.subtitles.keep_forced is True
        assert entry.subtitles.keep_sdh is True
        assert QualityConfig().preferred_container == "mkv"

    def test_container_is_normalised_and_validated(self):
        assert QualityConfig(preferred_container="MP4").preferred_container == "mp4"
        assert QualityConfig(preferred_container=".mkv").preferred_container == "mkv"
        with pytest.raises(ValidationError):
            QualityConfig(preferred_container="avi")

    def test_prefer_rejects_unknown_tokens(self):
        with pytest.raises(ValidationError):
            MediaPolicyEntry(subtitles={"prefer": ["text", "bitmapz"]})
        entry = MediaPolicyEntry(subtitles={"prefer": ["PGS", "srt"]})
        assert entry.subtitles.prefer == ["pgs", "srt"]

    def test_max_per_language_must_be_positive(self):
        with pytest.raises(ValidationError):
            MediaPolicyEntry(subtitles={"max_per_language": 0})

    def test_worker_selection_carries_policy_and_container(
        self, stack_config_with_temp_paths: StackConfig,
    ):
        cfg = stack_config_with_temp_paths.model_copy(deep=True)
        cfg.quality.preferred_container = "mp4"
        cfg.media_policy.movies.subtitles.max_per_language = 2
        cfg.media_policy.movies.subtitles.keep_sdh = False
        sel = PipelineWorker(cfg)._policy_for_category("movies")
        assert sel.container == "mp4"
        assert sel.subtitle_policy.max_per_language == 2
        assert sel.subtitle_policy.keep_sdh is False
        assert list(sel.subtitles) == ["eng"]


# ---------------------------------------------------------------------------
# Stream selection
# ---------------------------------------------------------------------------

class TestSubtitleSelection:
    def test_pirates_default_policy_keeps_one_normal_plus_sdh(self):
        assert _picked(pirates_streams(), _sel()) == [2, 3]

    def test_pirates_max_two_takes_next_codec_class(self):
        # Identical eng SRTs collapse; the 2nd normal slot goes to the PGS.
        assert _picked(pirates_streams(), _sel(max_per_language=2)) == [2, 4, 3]

    def test_pirates_prefer_image_first(self):
        assert _picked(pirates_streams(), _sel(prefer=["image", "text"])) == [4, 3]

    def test_pirates_no_sdh(self):
        assert _picked(pirates_streams(), _sel(keep_sdh=False)) == [2]

    def test_pirates_multiple_languages(self):
        # fre has two identical PGS -> one kept; spa not in policy.
        assert _picked(pirates_streams(), _sel(["eng", "fre"])) == [2, 3, 5]

    def test_language_codes_are_normalised(self):
        assert _picked(pirates_streams(), _sel(["eng", "fra"])) == [2, 3, 5]

    def test_exact_duplicates_collapse_even_under_high_limit(self):
        streams = [
            _video(0), _audio(1),
            _sub(2, "eng", "subrip"), _sub(3, "eng", "subrip"),
            _sub(4, "eng", "subrip"), _sub(5, "eng", "ass"),
        ]
        assert _picked(streams, _sel(max_per_language=5)) == [2, 5]

    def test_specific_codec_preference(self):
        streams = [_video(0), _sub(2, "eng", "subrip"), _sub(3, "eng", "ass")]
        assert _picked(streams, _sel(prefer=["ass", "srt"])) == [3]
        assert _picked(streams, _sel(prefer=["srt", "ass"])) == [2]

    def test_forced_kept_and_text_preferred(self):
        streams = [
            _video(0), _audio(1),
            _sub(2, "eng", PGS, forced=True),
            _sub(3, "eng", "subrip"),
            _sub(4, "eng", "subrip", forced=True),
            _sub(5, "spa", "subrip", forced=True),
        ]
        # Output order: forced, normal, sdh
        assert _picked(streams, _sel()) == [4, 3]
        assert _picked(streams, _sel(keep_forced=False)) == [3]

    def test_forced_detected_from_title(self):
        streams = [_video(0), _sub(2, "eng", "subrip"),
                   _sub(3, "eng", "subrip", title="Forced")]
        assert _picked(streams, _sel()) == [3, 2]
        assert _picked(streams, _sel(keep_forced=False)) == [2]

    def test_legacy_forced_pseudo_language_keeps_any_language_forced(self):
        streams = [_video(0), _sub(2, "eng", "subrip"),
                   _sub(3, "spa", "subrip", forced=True)]
        assert _picked(streams, _sel(["eng", "forced"])) == [2, 3]

    def test_sdh_disposition_and_title(self):
        streams = [_video(0), _sub(2, "eng", "subrip", hi=True),
                   _sub(3, "eng", "subrip"), _sub(4, "eng", PGS, title="SDH")]
        assert _picked(streams, _sel()) == [3, 2]

    def test_sdh_fills_normal_slot_when_only_sdh_exists_and_sdh_disabled(self):
        streams = [_video(0), _sub(2, "eng", PGS, title="English SDH")]
        assert _picked(streams, _sel(keep_sdh=False)) == [2]

    def test_commentary_dropped(self):
        streams = [_video(0), _sub(2, "eng", "subrip", title="Director's Commentary"),
                   _sub(3, "eng", PGS)]
        assert _picked(streams, _sel()) == [3]

    def test_unwanted_and_und_languages_dropped(self):
        streams = [_video(0), _sub(2, "und", "subrip"), _sub(3, "rus", "subrip")]
        assert _picked(streams, _sel()) == []

    def test_mkv_skips_mov_text(self):
        streams = [_video(0), _sub(2, "eng", "mov_text")]
        assert _picked(streams, _sel(), container="mkv") == []
        assert _picked(streams, _sel(), container="mp4") == [2]


class TestMp4Container:
    def test_mp4_keeps_text_and_records_nothing_when_text_available(self):
        result = select_subtitle_streams(pirates_streams(), _sel(container="mp4"), container="mp4")
        assert [int(t.stream_index) for t in result.selected] == [2, 3]
        assert result.dropped_for_container == []

    def test_mp4_drops_pgs_and_records_it(self, capsys):
        streams = [_video(0), _audio(1), _sub(2, "eng", PGS, title="SDH")]
        result = select_subtitle_streams(streams, _sel(container="mp4"), container="mp4")
        assert result.selected == []
        assert [int(t.stream_index) for t in result.dropped_for_container] == [2]
        assert "WARNING" in capsys.readouterr().out

    def test_mp4_falls_back_to_text_when_image_preferred(self):
        result = select_subtitle_streams(
            pirates_streams(), _sel(container="mp4", prefer=["image", "text"]), container="mp4",
        )
        assert [int(t.stream_index) for t in result.selected] == [2, 3]
        assert [int(t.stream_index) for t in result.dropped_for_container] == [4]

    def test_mp4_command_converts_text_to_mov_text(self):
        streams = pirates_streams()
        with patch("orchestrator.pipeline.remux.subprocess.run", return_value=_probe_ok(streams)):
            build = build_remux(Path("/in.mkv"), Path("/out.mp4"), _sel(container="mp4"))
        cmd = build.command
        assert _sub_maps(cmd, streams) == [2, 3]
        assert "-c:s" in cmd and cmd[cmd.index("-c:s") + 1] == "mov_text"
        assert cmd[-1] == "/out.mp4"

    def test_mp4_command_without_subs_has_no_subtitle_codec(self):
        streams = [_video(0), _audio(1), _sub(2, "eng", PGS)]
        with patch("orchestrator.pipeline.remux.subprocess.run", return_value=_probe_ok(streams)):
            build = build_remux(Path("/in.mkv"), Path("/out.mp4"), _sel(container="mp4"))
        assert _sub_maps(build.command, streams) == []
        assert "-c:s" not in build.command
        assert build.notes.dropped_image_subtitles  # recorded on the plan notes

    def test_worker_output_extension_follows_container(
        self, stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    ):
        src = temp_dir / "Some.Movie.2017.1080p.mkv"
        src.write_bytes(b"x")
        streams = [_video(0), _audio(1), _sub(2, "eng", PGS)]
        for container, ext in (("mkv", ".mkv"), ("mp4", ".mp4")):
            cfg = stack_config_with_temp_paths.model_copy(deep=True)
            cfg.quality.preferred_container = container
            worker = PipelineWorker(cfg)
            torrent = TorrentInfo(hash="h", name="Some.Movie.2017.1080p", category="movies",
                                  download_path=temp_dir, files=[src])
            with patch("orchestrator.pipeline.remux.subprocess.run",
                       return_value=_probe_ok(streams)):
                plan = worker.build_plans(torrent)[0]
            assert plan.final_output.suffix == ext
            assert plan.staging_output.suffix == ext
            assert plan.ffmpeg_command[-1] == str(plan.staging_output)
            if container == "mp4":
                assert plan.remux_notes.dropped_image_subtitles
            else:
                assert plan.remux_notes.dropped_image_subtitles == []


# ---------------------------------------------------------------------------
# Remux command (import path)
# ---------------------------------------------------------------------------

class TestRemuxCommand:
    def test_pirates_command_maps_only_policy_tracks(self):
        streams = pirates_streams()
        with patch("orchestrator.pipeline.remux.subprocess.run", return_value=_probe_ok(streams)):
            cmd = build_ffmpeg_command(Path("/in.mkv"), Path("/out.mkv"), _sel())
        assert _sub_maps(cmd, streams) == [2, 3]
        assert "0:s?" not in cmd
        assert cmd[-3:] == ["-c", "copy", "/out.mkv"]

    def test_probe_failure_fails_closed_on_subtitles(self):
        with patch("orchestrator.pipeline.remux.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
            build = build_remux(Path("/in.mkv"), Path("/out.mkv"), _sel())
        assert "0:s?" not in build.command
        assert not any(a.startswith("0:s") for a in build.command)
        assert "0:a?" in build.command  # audio fallback unchanged
        assert build.notes.probe_failed is True

    def test_probe_exception_fails_closed_on_subtitles(self):
        with patch("orchestrator.pipeline.remux.subprocess.run",
                   side_effect=OSError("ffprobe missing")):
            build = build_remux(Path("/in.mkv"), Path("/out.mkv"), _sel())
        assert not any(a.startswith("0:s") for a in build.command)
        assert build.notes.probe_failed is True

    def test_bdmv_concat_without_clpi_does_not_copy_all_subs(
        self, stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    ):
        worker = PipelineWorker(stack_config_with_temp_paths)
        cmd = worker._build_concat_command(
            temp_dir / "list.txt", temp_dir / "out.mkv", _sel(), stream_languages=None,
        )
        assert "0:s?" not in cmd

    def test_bdmv_concat_applies_policy_to_clpi_subs(
        self, stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    ):
        worker = PipelineWorker(stack_config_with_temp_paths)
        clpi = [
            {"type": "audio", "lang": "eng", "index": "1"},
            {"type": "subtitle", "lang": "eng", "index": "2"},
            {"type": "subtitle", "lang": "eng", "index": "3"},
            {"type": "subtitle", "lang": "fre", "index": "4"},
        ]
        cmd = worker._build_concat_command(
            temp_dir / "list.txt", temp_dir / "out.mkv", _sel(), stream_languages=clpi,
        )
        sub_maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map" and cmd[i + 1].startswith("0:s:")]
        assert sub_maps == ["0:s:0"]
        mp4 = worker._build_concat_command(
            temp_dir / "list.txt", temp_dir / "out.mp4", _sel(container="mp4"),
            stream_languages=clpi,
        )
        assert not any(a.startswith("0:s") for a in mp4)


# ---------------------------------------------------------------------------
# Cross-mux / video upgrade
# ---------------------------------------------------------------------------

def _cand_audio(index: str = "1", lang: str = "jpn") -> _AudioTrack:
    return _AudioTrack(stream_index=index, lang=lang, codec="ac3", profile="",
                       channels=6, bit_rate=0, title="", is_default=False,
                       is_commentary=False)


class TestCrossmuxPolicy:
    def test_crossmux_reapplies_policy_to_library_subs(self):
        lib = pirates_streams()
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=lib):
            cmd = build_crossmux_command(
                Path("/lib.mkv"), Path("/cand.mkv"), [_cand_audio()], None, 0.0,
                Path("/out.mkv"), selection=_sel(),
            )
        assert "0:s?" not in cmd
        assert _sub_maps(cmd, lib, input_idx=0) == [2, 3]

    def test_crossmux_candidate_sub_competes_with_library(self):
        lib = [_video(0), _audio(1), _sub(2, "eng", PGS)]
        cand_sub = _SubtitleCandidate(stream_index="5", lang="eng", codec="subrip")
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=lib):
            cmd = build_crossmux_command(
                Path("/lib.mkv"), Path("/cand.mkv"), [_cand_audio()], [cand_sub], 0.0,
                Path("/out.mkv"), selection=_sel(),
            )
        # Text candidate beats the library PGS under prefer=[text, image]
        assert _sub_maps(cmd, lib, input_idx=0) == []
        assert "1:5" in cmd
        assert cmd[cmd.index("-metadata:s:s:0") + 1] == "language=eng"

    def test_crossmux_candidate_forced_disposition_uses_output_index(self):
        lib = [_video(0), _audio(1), _sub(2, "eng", "subrip")]
        cand_sub = _SubtitleCandidate(stream_index="7", lang="eng", codec="subrip", forced=True)
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=lib):
            cmd = build_crossmux_command(
                Path("/lib.mkv"), Path("/cand.mkv"), [_cand_audio()], [cand_sub], 0.0,
                Path("/out.mkv"), selection=_sel(),
            )
        # forced sorts first -> output subtitle index 0
        assert cmd.index("1:7") < cmd.index("0:2")
        assert cmd[cmd.index("-disposition:s:s:0") + 1] == "forced"

    def test_crossmux_refuses_when_library_unprobeable(self):
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=None):
            with pytest.raises(RemuxPolicyError):
                build_crossmux_command(
                    Path("/lib.mkv"), Path("/cand.mkv"), [_cand_audio()], None, 0.0,
                    Path("/out.mkv"), selection=_sel(),
                )

    def test_video_upgrade_reapplies_policy(self):
        lib = pirates_streams()
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=lib):
            cmd = build_video_upgrade_command(
                Path("/lib.mkv"), Path("/cand.mkv"), 0.0, Path("/out.mkv"),
                selection=_sel(),
            )
        assert "1:s?" not in cmd
        assert _sub_maps(cmd, lib, input_idx=1) == [2, 3]

    def test_video_upgrade_refuses_when_library_unprobeable(self):
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=None):
            with pytest.raises(RemuxPolicyError):
                build_video_upgrade_command(
                    Path("/lib.mkv"), Path("/cand.mkv"), 0.0, Path("/out.mkv"),
                    selection=_sel(),
                )

    def test_crossmux_mp4_destination_converts_and_drops_image(self):
        lib = [_video(0), _audio(1), _sub(2, "eng", "mov_text"), _sub(3, "fre", "mov_text")]
        with patch("orchestrator.pipeline.remux.probe_raw_streams", return_value=lib):
            cmd = build_crossmux_command(
                Path("/lib.mp4"), Path("/cand.mkv"), [_cand_audio()],
                [_SubtitleCandidate(stream_index="4", lang="eng", codec=PGS, forced=True)],
                0.0, Path("/out.mp4"), selection=_sel(),
            )
        assert _sub_maps(cmd, lib, input_idx=0) == [2]
        assert "1:4" not in cmd
        assert cmd[cmd.index("-c:s") + 1] == "mov_text"


# ---------------------------------------------------------------------------
# Sweep compliance check
# ---------------------------------------------------------------------------

class TestSweepCompliance:
    def test_pirates_is_non_compliant(self):
        unwanted = unwanted_subtitle_streams(pirates_streams(), _sel(), container="mkv")
        assert len(unwanted) == 42

    def test_policy_compliant_file_is_clean(self):
        streams = [_video(0), _audio(1), _sub(2, "eng", "subrip"),
                   _sub(3, "eng", "subrip", hi=True)]
        assert unwanted_subtitle_streams(streams, _sel(), container="mkv") == []

    def test_scan_flags_bloated_file_and_reprobes_after_policy_change(
        self, stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    ):
        movie_dir = temp_dir / "pool" / "movies" / "Pirates (2017)"
        movie_dir.mkdir(parents=True)
        video = movie_dir / "Pirates (2017).mkv"
        video.write_bytes(b"x" * 2048)

        state: dict = {}
        repo = MagicMock()
        repo.load_pipeline_state.side_effect = lambda: state
        repo.save_pipeline_state.side_effect = lambda s: state.update(s)

        sweeper = LibrarySweeper(stack_config_with_temp_paths, repo)
        sweeper.pool_root = temp_dir / "pool"
        sweeper._arr_data_loaded = True

        with patch("orchestrator.pipeline.sweep.probe_raw_streams",
                   return_value=pirates_streams()):
            plan = sweeper.scan()
        assert plan.files_to_process == 1
        assert len(plan.actions[0].unwanted_subtitles) == 42

        # A file swept "ok" under an older policy must be re-checked.
        state["swept"] = {str(video): {"status": "ok", "mtime": video.stat().st_mtime}}
        with patch("orchestrator.pipeline.sweep.probe_raw_streams",
                   return_value=pirates_streams()):
            plan = sweeper.scan()
        assert plan.files_to_process == 1

    def test_sweep_execute_aborts_when_remux_probe_fails(
        self, stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    ):
        from orchestrator.pipeline.sweep import SweepAction, SweepPlan
        video = temp_dir / "pool" / "movies" / "X (2001)" / "X (2001).mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"x" * 2048)
        repo = MagicMock()
        repo.load_pipeline_state.return_value = {}
        sweeper = LibrarySweeper(stack_config_with_temp_paths, repo)
        sweeper._arr_data_loaded = True
        plan = SweepPlan(actions=[SweepAction(
            path=video, size=2048, category="movies", unwanted_audio=[],
            unwanted_subtitles=["#2 eng subrip"], selection=_sel(),
        )])
        with patch("orchestrator.pipeline.remux.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="")), \
             patch.object(sweeper, "_run_ffmpeg") as run_ffmpeg:
            result = sweeper.execute(plan)
        run_ffmpeg.assert_not_called()
        assert result.failed == 1 and result.succeeded == 0
        assert video.read_bytes() == b"x" * 2048
