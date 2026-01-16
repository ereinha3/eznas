"""Pipeline worker loop to remux completed torrents."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import httpx

from ..models import StackConfig
from ..storage import ConfigRepository
from .worker import PipelineWorker, TorrentInfo


@dataclass
class TorrentRecord:
    hash: str
    name: str
    category: str
    save_path: Path
    content_path: Path


class QbittorrentAPI:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client = httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))

    def close(self) -> None:
        self.client.close()

    def login(self) -> None:
        response = self.client.post(
            f"{self.base_url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        if response.text.strip() != "Ok.":
            raise RuntimeError("qBittorrent authentication failed")

    def list_completed(self) -> List[TorrentRecord]:
        response = self.client.get(
            f"{self.base_url}/api/v2/torrents/info",
            params={"filter": "completed"},
        )
        response.raise_for_status()
        items = response.json() or []
        records: List[TorrentRecord] = []
        for item in items:
            records.append(
                TorrentRecord(
                    hash=item.get("hash", ""),
                    name=item.get("name", ""),
                    category=item.get("category") or "",
                    save_path=Path(item.get("save_path") or ""),
                    content_path=Path(item.get("content_path") or ""),
                )
            )
        return [record for record in records if record.hash and record.save_path]

    def list_files(self, torrent_hash: str) -> List[Path]:
        response = self.client.get(
            f"{self.base_url}/api/v2/torrents/files",
            params={"hash": torrent_hash},
        )
        response.raise_for_status()
        files = response.json() or []
        return [Path(entry.get("name", "")) for entry in files if entry.get("name")]

    def remove_torrents(self, torrent_hashes: Iterable[str]) -> None:
        hashes = "|".join(torrent_hashes)
        if not hashes:
            return
        response = self.client.post(
            f"{self.base_url}/api/v2/torrents/delete",
            data={"hashes": hashes, "deleteFiles": "false"},
        )
        response.raise_for_status()


class PipelineRunner:
    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def run_forever(self, interval: float = 60.0) -> None:
        while True:
            try:
                self._tick()
            except Exception as exc:  # pragma: no cover - runtime safety net
                print(f"[pipeline] error: {exc}")
            time.sleep(interval)

    def _tick(self) -> None:
        config = self.repo.load_stack()
        if not config.services.pipeline.enabled:
            return

        qb_cfg = config.services.qbittorrent
        base_url = f"http://qbittorrent:{qb_cfg.port or 8080}"
        api = QbittorrentAPI(
            base_url=base_url,
            username=qb_cfg.username,
            password=qb_cfg.password,
        )
        try:
            api.login()
            torrents = api.list_completed()
            if not torrents:
                return
            for torrent in torrents:
                if not self._should_process(config, torrent.category):
                    continue
                if self._is_processed(torrent.hash):
                    continue
                self._process_torrent(api, config, torrent)
        finally:
            api.close()

    def _should_process(self, config: StackConfig, category: str) -> bool:
        categories = config.download_policy.categories
        return category in {categories.radarr, categories.sonarr, categories.anime}

    def _is_processed(self, torrent_hash: str) -> bool:
        state = self.repo.load_state()
        pipeline = state.get("pipeline", {})
        processed = pipeline.get("processed", {})
        return torrent_hash in processed

    def _mark_processed(self, torrent_hash: str, status: str) -> None:
        state = self.repo.load_state()
        pipeline = state.setdefault("pipeline", {})
        processed = pipeline.setdefault("processed", {})
        processed[torrent_hash] = {"status": status, "timestamp": int(time.time())}
        self.repo.save_state(state)

    def _process_torrent(
        self,
        api: QbittorrentAPI,
        config: StackConfig,
        torrent: TorrentRecord,
    ) -> None:
        files = api.list_files(torrent.hash)
        if not files:
            self._mark_processed(torrent.hash, "skipped_no_files")
            return
        download_path = torrent.save_path
        full_paths = [download_path / file for file in files]
        info = TorrentInfo(
            hash=torrent.hash,
            name=torrent.name,
            category=torrent.category,
            download_path=download_path,
            files=full_paths,
        )

        worker = PipelineWorker(config)
        plan = worker.build_plan(info)
        success = self._run_ffmpeg(plan.ffmpeg_command)
        if not success:
            self._mark_processed(torrent.hash, "ffmpeg_failed")
            return

        plan.final_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(plan.staging_output), str(plan.final_output))
        self._cleanup_path(torrent.content_path)
        api.remove_torrents([torrent.hash])
        self._mark_processed(torrent.hash, "ok")

    def _run_ffmpeg(self, command: List[str]) -> bool:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError as exc:
            print(f"[pipeline] ffmpeg failed to start: {exc}")
            return False
        if result.returncode != 0:
            stderr = result.stderr.strip()
            print(f"[pipeline] ffmpeg error: {stderr}")
            return False
        return True

    def _cleanup_path(self, path: Path) -> None:
        if not path:
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            print(f"[pipeline] cleanup failed: {exc}")


def main() -> None:
    root = Path(os.getenv("ORCH_ROOT", Path(__file__).resolve().parents[2]))
    repo = ConfigRepository(root)
    interval = float(os.getenv("PIPELINE_INTERVAL", "60"))
    runner = PipelineRunner(repo)
    runner.run_forever(interval=interval)


if __name__ == "__main__":
    main()


