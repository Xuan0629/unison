"""Persistent pipeline run history and best-effort legacy migration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_id(source: str, name: str, timestamp: str) -> str:
    raw = f"{source}\0{name}\0{timestamp}".encode("utf-8")
    return "legacy-" + hashlib.sha256(raw).hexdigest()[:16]


class RunHistoryStore:
    """Store one JSON record per pipeline run under ``.unison/runs``."""

    def __init__(
        self,
        project_root: Path,
        *,
        registry_file: Path | None = None,
        checkpoint_base: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.runs_dir = self.project_root / ".unison" / "runs"
        self.marker = self.runs_dir / ".legacy-migrated-v1"
        self.registry_file = registry_file or (
            Path.home() / ".unison" / "webui" / "projects.json"
        )
        self.checkpoint_base = checkpoint_base or (
            Path.home() / ".unison" / "checkpoints"
        )

    def _write(self, record: dict) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        target = self.runs_dir / f"{record['id']}.json"
        payload = json.dumps(record, indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.runs_dir, delete=False
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, target)

    def _read(self) -> list[dict]:
        if not self.runs_dir.exists():
            return []
        records: list[dict] = []
        for path in self.runs_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(record, dict) and record.get("id"):
                records.append(record)
        return records

    def start(self, run_id: str, *, pipeline_name: str, mode: str) -> None:
        self._write({
            "id": run_id,
            "pipeline_name": pipeline_name,
            "mode": mode,
            "status": "running",
            "phase": "init",
            "iteration": 0,
            "verdict": None,
            "commit": None,
            "started_at": _now(),
            "finished_at": None,
            "legacy": False,
            "source": "native",
        })

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        phase: str,
        iteration: int,
        verdict: str | None,
        commit: str | None,
        halt_reason: str | None = None,
    ) -> None:
        target = self.runs_dir / f"{run_id}.json"
        record: dict = {}
        if target.exists():
            try:
                record = json.loads(target.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                record = {}
        record.update({
            "id": run_id,
            "status": status,
            "phase": phase,
            "iteration": iteration,
            "verdict": verdict,
            "commit": commit,
            "halt_reason": halt_reason,
            "finished_at": _now(),
            "legacy": False,
            "source": "native",
        })
        record.setdefault("pipeline_name", run_id)
        record.setdefault("mode", None)
        record.setdefault("started_at", record["finished_at"])
        self._write(record)

    def list_runs(self, *, migrate: bool = True) -> list[dict]:
        if migrate and not self.marker.exists():
            self._migrate_legacy()
        records = self._read()
        return sorted(
            records,
            key=lambda r: r.get("finished_at") or r.get("started_at") or "",
            reverse=True,
        )

    def _migrate_legacy(self) -> None:
        existing_records = self._read()
        existing_names = {
            r["pipeline_name"] for r in existing_records if r.get("pipeline_name")
        }

        notification_records = self._notification_records()
        for record in notification_records:
            self._write(record)
        imported_names = existing_names | {
            record["pipeline_name"] for record in notification_records
        }

        for record in self._run_log_records():
            if record["pipeline_name"] not in imported_names:
                self._write(record)
                imported_names.add(record["pipeline_name"])

        for record in self._checkpoint_records():
            if record["pipeline_name"] not in imported_names:
                self._write(record)
                imported_names.add(record["pipeline_name"])

        for record in self._pipeline_yaml_records():
            if record["pipeline_name"] not in imported_names:
                self._write(record)
                imported_names.add(record["pipeline_name"])

        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.marker.write_text(_now(), encoding="utf-8")

    def _notification_records(self) -> list[dict]:
        path = self.project_root / "observer" / "notifications.jsonl"
        if not path.exists():
            return []
        active: dict[str, dict] = {}
        records: list[dict] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = event.get("pipeline")
            event_type = event.get("event_type")
            if not name or event_type not in {"pipeline_start", "pipeline_done", "halted"}:
                continue
            timestamp = event.get("timestamp", "")
            if event_type == "pipeline_start":
                if name in active:
                    records.append(active.pop(name))
                active[name] = {
                    "id": _legacy_id("notifications", name, timestamp),
                    "pipeline_name": name,
                    "mode": None,
                    "status": "running",
                    "phase": event.get("phase") or "init",
                    "iteration": event.get("iteration", 0),
                    "verdict": event.get("verdict") or None,
                    "commit": None,
                    "started_at": timestamp,
                    "finished_at": None,
                    "halt_reason": None,
                    "legacy": True,
                    "source": "notifications",
                }
                continue

            record = active.pop(name, {
                "id": _legacy_id("notifications", name, timestamp),
                "pipeline_name": name,
                "mode": None,
                "started_at": timestamp,
                "commit": None,
                "legacy": True,
                "source": "notifications",
            })
            record.update({
                "status": "done" if event_type == "pipeline_done" else "halted",
                "phase": event.get("phase") or record.get("phase") or "unknown",
                "iteration": event.get("iteration", record.get("iteration", 0)),
                "verdict": event.get("verdict") or record.get("verdict"),
                "finished_at": timestamp,
                "halt_reason": (
                    event.get("summary") or event.get("body")
                    if event_type == "halted" else None
                ),
            })
            records.append(record)

        records.extend(active.values())
        return records

    def _basename_is_unique(self) -> bool:
        if not self.registry_file.exists():
            return True
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True
        projects = raw.get("projects", []) if isinstance(raw, dict) else []
        matches = [
            project for project in projects
            if isinstance(project, dict)
            and project.get("path")
            and Path(project["path"]).resolve().name == self.project_root.name
        ]
        return len(matches) <= 1

    def _checkpoint_records(self) -> list[dict]:
        # Legacy checkpoint storage used only basename. It is safe to import
        # only while the WebUI registry has no second project with that name.
        if not self._basename_is_unique():
            return []
        checkpoint_dir = self.checkpoint_base / self.project_root.name
        if not checkpoint_dir.exists():
            return []
        latest: dict[str, tuple[float, dict]] = {}
        for path in checkpoint_dir.glob("ckpt-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            name = state.get("pipeline_name")
            if not name:
                continue
            mtime = path.stat().st_mtime
            if name not in latest or mtime > latest[name][0]:
                latest[name] = (mtime, state)
        records = []
        for name, (mtime, state) in latest.items():
            timestamp = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
            status = "halted" if state.get("halt_signal") else (
                "done" if state.get("phase") == "done" else "unknown"
            )
            records.append({
                "id": _legacy_id("checkpoints", name, timestamp),
                "pipeline_name": name,
                "mode": None,
                "status": status,
                "phase": state.get("phase") or "init",
                "iteration": state.get("iteration", 0),
                "verdict": state.get("last_review_verdict"),
                "commit": state.get("last_dev_commit"),
                "started_at": state.get("history", [{}])[0].get("timestamp") if state.get("history") else timestamp,
                "finished_at": state.get("last_activity") or timestamp,
                "halt_reason": state.get("halt_reason"),
                "legacy": True,
                "source": "checkpoints",
            })
        return records

    def _run_log_records(self) -> list[dict]:
        path = self.project_root / ".unison" / "run.log"
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        header = re.compile(r"^===\s+([^=\n]+?)\s+(\d{4}[^=\n]+?)\s+===\s*$", re.MULTILINE)
        matches = list(header.finditer(text))
        records: list[dict] = []
        for index, match in enumerate(matches):
            name = match.group(1).strip()
            if name.upper() in {"START", "DONE"} or "DONE" in name.upper():
                continue
            block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            block = text[match.end():block_end]
            phase_match = re.search(r"Final phase:\s*(\S+)", block)
            exit_match = re.search(r"EXIT:(\d+)", block)
            if not phase_match and not exit_match:
                continue
            exit_code = int(exit_match.group(1)) if exit_match else None
            status = "done" if exit_code == 0 else ("halted" if exit_code == 2 else "unknown")
            iteration = re.search(r"Iteration:\s*(\d+)", block)
            verdict = re.search(r"Last verdict:\s*(\S+)", block)
            commit = re.search(r"Last commit:\s*(\S+)", block)
            timestamp = match.group(2).strip()
            records.append({
                "id": _legacy_id("run.log", name, timestamp),
                "pipeline_name": name,
                "mode": None,
                "status": status,
                "phase": phase_match.group(1) if phase_match else "unknown",
                "iteration": int(iteration.group(1)) if iteration else 0,
                "verdict": verdict.group(1) if verdict else None,
                "commit": commit.group(1) if commit else None,
                "started_at": timestamp,
                "finished_at": timestamp,
                "halt_reason": None,
                "legacy": True,
                "source": "run.log",
            })
        return records

    def _pipeline_yaml_records(self) -> list[dict]:
        candidates = list(self.project_root.glob("*.yaml"))
        candidates += list((self.project_root / "pipelines").glob("*.yaml"))
        records = []
        for path in candidates:
            try:
                import yaml
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict) or "agents" not in raw:
                continue
            project_raw = raw.get("project")
            project: dict = project_raw if isinstance(project_raw, dict) else {}
            name = project.get("name") or path.stem
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            records.append({
                "id": _legacy_id("pipeline-yaml", name, timestamp),
                "pipeline_name": name,
                "mode": raw.get("mode"),
                "status": "unknown",
                "phase": "unknown",
                "iteration": 0,
                "verdict": None,
                "commit": None,
                "started_at": timestamp,
                "finished_at": timestamp,
                "halt_reason": None,
                "legacy": True,
                "source": "pipeline-yaml",
            })
        return records
