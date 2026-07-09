"""observer.py — Observer: FileWatcher + liveness + notification dual-write."""

from __future__ import annotations

import ctypes
import errno
import json
import logging
import os
import queue
import shlex
import struct
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from unison.world import World
from unison.state import State
from unison.event_bus import get_event_bus
from unison.interfaces import Notification

logger = logging.getLogger(__name__)


# ============================================================================
# P10: Message templates (en / zh)
# ============================================================================

_MESSAGES: dict[str, dict[str, str]] = {
    # ---- Pipeline lifecycle ----
    "pipeline_start": {
        "en": "🔵 {pipeline} started | {mode} | {agent_count} agents",
        "zh": "🔵 {pipeline} — 已启动 | {mode} | {agent_count} 个 agent",
    },
    "pipeline_done": {
        "en": "✅ {pipeline} complete — {commits} commits, {tests} tests",
        "zh": "✅ {pipeline} 完成 — {commits} 次提交, {tests} 个测试",
    },
    # ---- Phase lifecycle ----
    "phase_done": {
        "en": "🟢 {phase} passed ({iteration} iters) | verdict: {verdict}{commits_detail}",
        "zh": "🟢 {phase} 阶段通过 ({iteration} 轮) | 判定: {verdict}{commits_detail}",
    },
    "phase_changes": {
        "en": "🟡 {phase} REQUEST_CHANGES ({iteration} iters) | auto-advancing...",
        "zh": "🟡 {phase} 要求修改 ({iteration} 轮) | 自动推进中...",
    },
    # ---- Stalled / halted ----
    "stalled": {
        "en": "⚠️ Session stalled — no activity for {elapsed}s in phase {phase}",
        "zh": "⚠️ 会话停滞 — {phase} 阶段已 {elapsed} 秒无活动",
    },
    "halted": {
        "en": "🔴 Pipeline halted: {reason} | phase: {phase} iter: {iteration}",
        "zh": "🔴 管道终止: {reason} | 阶段: {phase} 轮次: {iteration}",
    },
    # ---- Observer banner ----
    "observer_banner": {
        "en": "📡 Unison Observer",
        "zh": "📡 Unison 观察者",
    },
}


def _msg(key: str, language: str, **kwargs) -> str:
    """Look up message template by *key* and *language*, format with kwargs.

    Falls back to 'en' template if the requested language is missing a key.
    """
    templates = _MESSAGES.get(key, {})
    template = templates.get(language) or templates.get("en", key)
    return template.format(**kwargs)


# ============================================================================
# FileEvent
# ============================================================================


@dataclass(frozen=True)
class FileEvent:
    """文件变化事件。

    Attributes:
        path: 变化的文件路径。
        event_type: 事件类型 — created, modified, deleted, overflow。
        timestamp: ISO 8601 时间戳。
    """

    path: Path
    event_type: Literal["created", "modified", "deleted", "overflow"]
    timestamp: str  # ISO 8601


# ============================================================================
# FileWatcher Protocol
# ============================================================================


class FileWatcher:
    """文件监控器接口（structural subtyping — 不需要显式继承）。

    实现类必须提供 watch, next_event, stop 三个方法。
    """

    def watch(self, paths: list[Path]) -> None:
        """开始监控指定目录列表。

        Args:
            paths: 要监控的目录路径列表。
        """
        ...  # pragma: no cover

    def next_event(self, timeout_seconds: float = 1.0) -> FileEvent | None:
        """阻塞等待下一个事件，超时返回 None。

        Args:
            timeout_seconds: 等待超时（秒）。

        Returns:
            FileEvent 如果检测到变化，否则 None。
            返回 event_type="overflow" 表示队列满，调用方应全量重新扫描。
        """
        ...  # pragma: no cover

    def stop(self) -> None:
        """停止监控，释放资源。"""
        ...  # pragma: no cover


# ============================================================================
# InotifyWatcher (Linux)
# ============================================================================


class _EpollEvent(ctypes.Structure):
    """epoll_event 结构体 (x86_64 Linux ABI)。"""
    _fields_ = [
        ("events", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),  # padding for alignment
        ("data_fd", ctypes.c_int),
        ("_reserved", ctypes.c_int),
    ]


class InotifyWatcher:
    """Linux inotify + epoll 文件监控器。

    使用 ctypes 调用 inotify/epoll 系统调用，零外部依赖。
    监控父目录而非文件，按文件名过滤事件（避免 atomic_write inode 陷阱）。
    """

    # inotify flags
    IN_CLOEXEC = 0o2000000
    IN_NONBLOCK = 0o4000

    # inotify event masks
    IN_CLOSE_WRITE = 0x00000008
    IN_CREATE = 0x00000100
    IN_DELETE = 0x00000200
    IN_MOVED_TO = 0x00000080
    IN_MOVED_FROM = 0x00000040
    IN_Q_OVERFLOW = 0x00004000
    IN_IGNORED = 0x00008000

    # epoll flags
    EPOLLIN = 0x001
    EPOLL_CTL_ADD = 1
    EPOLL_CTL_DEL = 2

    # Watched events
    WATCH_MASK = IN_CLOSE_WRITE | IN_CREATE | IN_DELETE | IN_MOVED_TO | IN_MOVED_FROM

    _EVENT_FMT = "iIII"  # wd, mask, cookie, len
    _EVENT_SIZE = struct.calcsize(_EVENT_FMT)  # 16

    def __init__(self) -> None:
        if sys.platform != "linux":
            raise OSError(errno.ENOSYS, "InotifyWatcher requires Linux")

        self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self._setup_ctypes()

        self._inotify_fd: int = -1
        self._epoll_fd: int = -1
        self._wd_to_path: dict[int, Path] = {}
        self._running = False
        self._event_queue: list[FileEvent] = []

        # inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        fd = self._libc.inotify_init1(self.IN_NONBLOCK | self.IN_CLOEXEC)
        if fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        self._inotify_fd = fd

        # epoll_create1(0)
        epfd = self._libc.epoll_create1(0)
        if epfd < 0:
            err = ctypes.get_errno()
            os.close(self._inotify_fd)
            self._inotify_fd = -1
            raise OSError(err, os.strerror(err))
        self._epoll_fd = epfd

        # epoll_ctl(ADD, inotify_fd)
        ev = _EpollEvent()
        ev.events = self.EPOLLIN
        ev.data_fd = self._inotify_fd
        if self._libc.epoll_ctl(self._epoll_fd, self.EPOLL_CTL_ADD,
                                self._inotify_fd, ctypes.byref(ev)) < 0:
            err = ctypes.get_errno()
            os.close(self._epoll_fd)
            os.close(self._inotify_fd)
            self._epoll_fd = -1
            self._inotify_fd = -1
            raise OSError(err, os.strerror(err))

    # ---- ctypes setup --------------------------------------------------------

    def _setup_ctypes(self) -> None:
        """配置 libc 函数签名。"""
        self._libc.inotify_init1.argtypes = [ctypes.c_int]
        self._libc.inotify_init1.restype = ctypes.c_int

        self._libc.inotify_add_watch.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32,
        ]
        self._libc.inotify_add_watch.restype = ctypes.c_int

        self._libc.epoll_create1.argtypes = [ctypes.c_int]
        self._libc.epoll_create1.restype = ctypes.c_int

        self._libc.epoll_ctl.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(_EpollEvent),
        ]
        self._libc.epoll_ctl.restype = ctypes.c_int

        self._libc.epoll_wait.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(_EpollEvent),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._libc.epoll_wait.restype = ctypes.c_int

    # ---- public API ----------------------------------------------------------

    def watch(self, paths: list[Path]) -> None:
        """开始监控指定目录列表。

        为每个目录添加 inotify watch。如果 inotify_add_watch 返回 ENOSPC，
        设置降级标志，后续 next_event() 将始终返回 None。

        Args:
            paths: 要监控的目录路径列表。
        """
        for path in paths:
            # Determine watch directory: existing files → watch parent dir;
            # non-existent or existing directories → watch the path itself.
            if path.exists() and path.is_file():
                watch_dir = path.parent
            else:
                watch_dir = path
            if not watch_dir.exists():
                watch_dir.mkdir(parents=True, exist_ok=True)

            wd = self._libc.inotify_add_watch(
                self._inotify_fd,
                str(watch_dir).encode(),
                self.WATCH_MASK,
            )
            if wd < 0:
                err = ctypes.get_errno()
                if err == errno.ENOSPC:
                    logger.warning(
                        "inotify_add_watch(%s): %s — raising for fallback",
                        watch_dir, os.strerror(err),
                    )
                    # Raise ENOSPC so Observer.run() can switch to
                    # PollingWatcher. Swallowing would leave the
                    # inotify watcher with zero watches and no way
                    # to detect file changes (Codex Iter 3).
                    raise OSError(err, os.strerror(err))
                raise OSError(err, os.strerror(err))

            self._wd_to_path[wd] = watch_dir

        self._running = True

    def next_event(self, timeout_seconds: float = 1.0) -> FileEvent | None:
        """阻塞等待下一个文件事件。

        Args:
            timeout_seconds: epoll_wait 超时（秒）。

        Returns:
            FileEvent 或 None（超时/已停止/降级）。
        """
        if not self._running:
            return None

        # 优先返回已排队事件
        if self._event_queue:
            return self._event_queue.pop(0)

        timeout_ms = int(timeout_seconds * 1000)

        # epoll_wait
        events = (_EpollEvent * 1)()
        while True:
            n = self._libc.epoll_wait(self._epoll_fd, events, 1, timeout_ms)
            if n < 0:
                err = ctypes.get_errno()
                if err == errno.EINTR:
                    if not self._running:
                        return None
                    continue
                raise OSError(err, os.strerror(err))
            break

        if n == 0:
            return None  # timeout

        # 从 inotify fd 读取事件
        try:
            buf = os.read(self._inotify_fd, 4096)
        except BlockingIOError:
            return None

        self._parse_inotify_buffer(buf)

        if self._event_queue:
            return self._event_queue.pop(0)
        return None

    def stop(self) -> None:
        """停止监控，关闭文件描述符。"""
        self._running = False
        if self._inotify_fd >= 0:
            try:
                os.close(self._inotify_fd)
            except OSError:
                pass
            self._inotify_fd = -1
        if self._epoll_fd >= 0:
            try:
                os.close(self._epoll_fd)
            except OSError:
                pass
            self._epoll_fd = -1

    # ---- internal ------------------------------------------------------------

    def _parse_inotify_buffer(self, buf: bytes) -> None:
        """解析 inotify 缓冲区，生成 FileEvent 加入队列。

        Args:
            buf: 从 inotify fd 读取的原始字节。
        """
        pos = 0
        while pos + self._EVENT_SIZE <= len(buf):
            wd, mask, cookie, name_len = struct.unpack_from(
                self._EVENT_FMT, buf, pos,
            )

            # IN_Q_OVERFLOW — 队列满，返回 overflow 事件
            if mask & self.IN_Q_OVERFLOW:
                self._event_queue.append(FileEvent(
                    path=Path("."),
                    event_type="overflow",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
                pos += self._EVENT_SIZE + name_len
                continue

            # IN_IGNORED — watch 被内核移除
            if mask & self.IN_IGNORED:
                pos += self._EVENT_SIZE + name_len
                continue

            # 解析文件名
            name = ""
            if name_len > 0:
                raw_name = buf[pos + self._EVENT_SIZE:
                               pos + self._EVENT_SIZE + name_len]
                name = raw_name.rstrip(b"\x00").decode(errors="replace")

            # 确定事件类型
            event_type = self._mask_to_event_type(mask)

            if event_type and name:
                watch_dir = self._wd_to_path.get(wd)
                if watch_dir is not None:
                    self._event_queue.append(FileEvent(
                        path=watch_dir / name,
                        event_type=event_type,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))

            pos += self._EVENT_SIZE + name_len

    @staticmethod
    def _mask_to_event_type(mask: int) -> str | None:
        """将 inotify mask 映射为 FileEvent event_type。"""
        if mask & (InotifyWatcher.IN_CREATE | InotifyWatcher.IN_MOVED_TO):
            return "created"
        elif mask & InotifyWatcher.IN_CLOSE_WRITE:
            return "modified"
        elif mask & (InotifyWatcher.IN_DELETE | InotifyWatcher.IN_MOVED_FROM):
            return "deleted"
        return None

    def __del__(self) -> None:
        self.stop()


# ============================================================================
# PollingWatcher (fallback)
# ============================================================================


class PollingWatcher:
    """轮询文件监控器（macOS/Windows fallback，或 Linux ENOSPC 降级）。

    通过比较文件 mtime 检测变化。纯标准库实现，无外部依赖。
    """

    def __init__(self, interval_seconds: float = 5.0) -> None:
        """
        Args:
            interval_seconds: 扫描间隔（秒）。默认 5s。
        """
        self._interval = interval_seconds
        self._paths: list[Path] = []
        self._known_files: dict[Path, float] = {}  # path → mtime
        self._last_scan = 0.0
        self._running = False
        self._event_queue: list[FileEvent] = []

    def watch(self, paths: list[Path]) -> None:
        """开始监控指定目录列表。

        Args:
            paths: 要监控的目录路径列表。
        """
        self._paths = list(paths)
        self._running = True
        self._scan(initial=True)

    def next_event(self, timeout_seconds: float = 1.0) -> FileEvent | None:
        """阻塞等待下一个文件事件。

        如果距上次扫描超过 interval_seconds，执行扫描并比较文件状态。

        Args:
            timeout_seconds: 等待超时（秒）。

        Returns:
            FileEvent 或 None。
        """
        if not self._running:
            return None

        # 队列中有事件，直接返回
        if self._event_queue:
            return self._event_queue.pop(0)

        # 检查是否到了扫描时间
        if time.monotonic() - self._last_scan >= self._interval:
            self._scan()

        if self._event_queue:
            return self._event_queue.pop(0)

        # 无事发生，短暂休眠
        time.sleep(min(timeout_seconds, 0.5))
        return None

    def stop(self) -> None:
        """停止监控。"""
        self._running = False

    # ---- internal ------------------------------------------------------------

    def _scan(self, initial: bool = False) -> None:
        """扫描所有监控目录，检测文件变化。

        Args:
            initial: 是否为首轮扫描（不产生事件，仅建立基线）。
        """
        current_files: dict[Path, float] = {}

        for watch_dir in self._paths:
            if not watch_dir.exists():
                continue
            try:
                for entry in watch_dir.iterdir():
                    if entry.is_file():
                        try:
                            current_files[entry] = entry.stat().st_mtime
                        except OSError:
                            continue
            except OSError:
                continue

        if not initial:
            # 检测新增 / 修改
            for path, mtime in current_files.items():
                if path not in self._known_files:
                    self._event_queue.append(FileEvent(
                        path=path,
                        event_type="created",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))
                elif mtime != self._known_files[path]:
                    self._event_queue.append(FileEvent(
                        path=path,
                        event_type="modified",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))

            # 检测删除
            for path in self._known_files:
                if path not in current_files:
                    self._event_queue.append(FileEvent(
                        path=path,
                        event_type="deleted",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))

        self._known_files = current_files
        self._last_scan = time.monotonic()


# ============================================================================
# MockWatcher (testing)
# ============================================================================


class MockWatcher:
    """测试用 watcher，手动注入事件。"""

    def __init__(self) -> None:
        self._events: list[FileEvent] = []
        self._watched_paths: list[Path] = []
        self._stopped = False

    def watch(self, paths: list[Path]) -> None:
        """记录被监控的路径。

        Args:
            paths: 要监控的目录路径列表。
        """
        self._watched_paths = list(paths)

    def inject_event(self, event: FileEvent) -> None:
        """测试注入事件。

        Args:
            event: 要注入的 FileEvent。
        """
        self._events.append(event)

    def next_event(self, timeout_seconds: float = 1.0) -> FileEvent | None:
        """返回下一个被注入的事件。

        Args:
            timeout_seconds: 等待超时（秒）。

        Returns:
            FileEvent 或 None。
        """
        if self._stopped:
            return None
        if self._events:
            return self._events.pop(0)
        time.sleep(min(timeout_seconds, 0.1))
        return None

    def stop(self) -> None:
        """标记为已停止。"""
        self._stopped = True


# ============================================================================
# Observer
# ============================================================================


class Observer:
    """独立进程。监控 state.json + notifications.jsonl → 通知 Discord。

    使用 FileWatcher 监控文件变化，支持 inotify（Linux）和 polling（fallback）。
    """

    def __init__(
        self,
        world: World,
        stall_threshold_seconds: int = 300,
        poll_interval: int = 60,
        watcher: InotifyWatcher | PollingWatcher | MockWatcher | None = None,
    ) -> None:
        """
        Args:
            world: 项目工作区布局。
            stall_threshold_seconds: 停滞阈值（秒）。超过此时间无活动视为 stalled。
            poll_interval: 轮询间隔（秒）。用于 next_event 超时 + liveness 检查。
            watcher: 文件监控器。None 则根据平台自动选择。
        """
        self.world = world
        self.stall_threshold_seconds = stall_threshold_seconds
        self._poll_interval = poll_interval
        self.watcher = watcher if watcher is not None else self._create_default_watcher()
        self._running = False
        self._use_polling = False
        self._notification_offset = 0
        self._event_queue: queue.Queue[dict] = queue.Queue()  # event bus → main loop bridge
        # P10: Language + pipeline name — read from state.json on startup
        self.observer_language: str = "en"
        self.pipeline_name: str = ""

    # ---- public API ----------------------------------------------------------

    def run(self) -> None:
        """阻塞循环。监控 state.json + notifications.jsonl。Ctrl-C 退出。

        监控父目录而非文件，按文件名过滤（避免 atomic_write inode 陷阱）。

        V2: Uses a select-style timed loop — on timeout (no events for
        ``_poll_interval`` seconds), runs a liveness check on the
        current state.  Falls back to polling mode on ENOSPC.

        Phase 6: Subscribes to the internal event bus for real-time phase
        change notifications.  The file watcher is kept as a polling
        fallback for notifications.jsonl and for when the event bus
        is unavailable (e.g. observer running in a separate process).

        Raises:
            RuntimeError: state.json 缺失时。
        """
        # 确保目录存在
        self.world.ensure_directories()

        # ---- Write PID file for liveness monitoring -----------------------------
        pid_dir = Path.home() / ".unison" / "observer"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / f"{self.world.root.name}.pid"
        pid_file.write_text(str(os.getpid()))
        try:
            self._run_loop()
        finally:
            pid_file.unlink(missing_ok=True)

    def _run_loop(self) -> None:
        """Blocking event loop (extracted so PID cleanup is guaranteed)."""
        # ---- P10: Read language + pipeline name from state.json if available ----
        self._load_config_from_state()

        # ---- Phase 6: subscribe to internal event bus -------------------------
        bus = get_event_bus()
        bus.subscribe("phase", self._on_phase_event)

        # 监控两个父目录（保留文件监控作为 fallback）
        paths = [self.world.unison_dir, self.world.observer_dir]
        try:
            self.watcher.watch(paths)
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOSPC or "ENOSPC" in str(e):
                logger.warning(
                    "ENOSPC on watch, switching to polling watcher"
                )
                self._use_polling = True
                # Replace the inotify watcher with a real polling watcher.
                # Without this, the InotifyWatcher with no registered
                # watches would return None from every next_event() call
                # and never detect file changes (Codex Iter 3 finding).
                self.watcher = PollingWatcher()
                self.watcher.watch(paths)
            else:
                raise

        self._running = True
        while self._running:
            # ---- Check event bus queue first (non-blocking) -------------------
            try:
                bus_event = self._event_queue.get_nowait()
                # Process phase event directly — no need to read state.json
                phase = bus_event.get("phase", "")
                if not self._check_liveness_from_event(bus_event):
                    lang = self.observer_language
                    body = _msg("stalled", lang,
                                elapsed=self.stall_threshold_seconds,
                                phase=phase)
                    self._emit_event(
                        event_type="stalled",
                        phase=phase,
                        severity="warn",
                        title=body,
                        body=body,
                        summary=body,
                    )
                continue
            except queue.Empty:
                pass

            # ---- Fallback: file watcher (polling for notifications.jsonl) ------
            event = self.watcher.next_event(timeout_seconds=self._poll_interval)

            if event is None:
                # Timed out — check liveness (poll fallback)
                if self.world.state_file.exists():
                    try:
                        state = State.atomic_read(self.world.state_file)
                        if not self.check_liveness(state):
                            lang = self.observer_language
                            body = _msg("stalled", lang,
                                        elapsed=self.stall_threshold_seconds,
                                        phase=state.phase)
                            self._emit_event(
                                event_type="stalled",
                                phase=state.phase,
                                severity="warn",
                                title=body,
                                body=body,
                                summary=body,
                            )
                        # P10: Check for SKIP intervention on every state read
                        self._check_skip_intervention(state)
                    except Exception:
                        pass
                continue

            # 处理 overflow 事件（inotify 队列满）
            if event.event_type == "overflow":
                self._full_rescan()
                continue

            # 过滤非目标文件事件
            if event.path.name not in ("state.json", "notifications.jsonl"):
                continue

            # 处理 state.json 变化（poll fallback）
            if event.path.name == "state.json":
                if not self.world.state_file.exists():
                    raise RuntimeError("state.json missing, cannot continue")

                state = State.atomic_read(self.world.state_file)
                if not self.check_liveness(state):
                    lang = self.observer_language
                    body = _msg("stalled", lang,
                                elapsed=self.stall_threshold_seconds,
                                phase=state.phase)
                    self._emit_event(
                        event_type="stalled",
                        phase=state.phase,
                        severity="warn",
                        title=body,
                        body=body,
                        summary=body,
                    )

                # P10: Check for SKIP intervention on every state.json change
                self._check_skip_intervention(state)

            # 处理 notifications.jsonl 变化
            if event.path.name == "notifications.jsonl":
                if not self.world.notifications_file.exists():
                    # 非关键，重建即可
                    self.world.notifications_file.parent.mkdir(
                        parents=True, exist_ok=True,
                    )
                    self.world.notifications_file.touch()
                else:
                    self._process_new_notifications()

        # ---- Phase 6: unsubscribe on stop --------------------------------------
        bus.unsubscribe("phase", self._on_phase_event)
        self.watcher.stop()

    def stop(self) -> None:
        """停止 Observer 循环。"""
        self._running = False
        self.watcher.stop()

    # ---- Phase 6: event bus callbacks ------------------------------------------

    def _load_config_from_state(self) -> None:
        """P10: Read observer_language + pipeline_name from state.json.

        Called once on startup.  If state.json doesn't exist yet, defaults
        are kept (en / empty string).
        """
        if not self.world.state_file.exists():
            return
        try:
            state = State.atomic_read(self.world.state_file)
            lang = getattr(state, "observer_language", "en")
            if lang in ("en", "zh"):
                self.observer_language = lang
            self.pipeline_name = getattr(state, "pipeline_name", "")
        except Exception:
            pass  # Non-fatal — use defaults

    # ---- P10: SKIP intervention -----------------------------------------------

    _SKIP_CONSECUTIVE_THRESHOLD: int = 3  # Trigger after 3+ REQUEST_CHANGES

    def _check_skip_intervention(self, state: State) -> None:
        """P10: Check if the pipeline is stuck in REQUEST_CHANGES loop.

        When 3+ consecutive review verdicts are REQUEST_CHANGES in the
        dev-review phase, check whether the current output minimally
        satisfies the user's needs.  If so, write the skip control file
        so the orchestrator can exit the loop on the next iteration
        boundary.

        Conditions:
        - 3+ consecutive REQUEST_CHANGES in state.history (dev_review)
        - At least one output file exists (PRD or test results)
        - Test command passes (if configured)

        The orchestrator's ``_check_control_files()`` already reads and
        consumes ``.unison/control/skip.json`` at every iteration
        boundary.
        """
        # Only intervene in dev-review phases
        if "dev" not in state.phase:
            return

        # Check for 3+ consecutive REQUEST_CHANGES in history
        consecutive = 0
        for t in reversed(state.history):
            if t.to_phase and "review" in t.to_phase and t.verdict == "REQUEST_CHANGES":
                consecutive += 1
                if consecutive >= self._SKIP_CONSECUTIVE_THRESHOLD:
                    break
            elif t.to_phase and "review" in t.to_phase:
                # A PASS in review resets the counter — stop scanning
                consecutive = 0
                break
            # Non-review transitions don't reset the counter
        if consecutive < self._SKIP_CONSECUTIVE_THRESHOLD:
            return

        # --- Threshold met — check minimal satisfaction ---
        root = self.world.root
        # Has the pipeline produced any output?
        has_prd = (root / "prd" / "PRD.md").exists()
        has_specs = (root / "prd" / "specs").exists()
        has_any_output = has_prd or has_specs

        if not has_any_output:
            logger.info("SKIP intervention: no output detected, skipping")
            return

        # Run test command if configured
        test_cmd = self._read_test_command()
        if test_cmd:
            try:
                result = subprocess.run(
                    shlex.split(test_cmd) if isinstance(test_cmd, str) else test_cmd,
                    cwd=str(root),
                    capture_output=True, timeout=120,
                )
                if result.returncode != 0:
                    logger.info(
                        "SKIP intervention: test command failed (exit %d), "
                        "not skipping", result.returncode
                    )
                    return
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("SKIP intervention: test command error: %s", exc)
                return

        # --- Conditions met: write skip control file ---
        self._write_skip_control(state)

    def _read_test_command(self) -> str | None:
        """P10: Read test_command from pipeline config or project config."""
        # Try to read from pipeline.yaml
        pipeline_file = self.world.root / "pipeline.yaml"
        if pipeline_file.exists():
            try:
                import yaml
                raw = yaml.safe_load(pipeline_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    project = raw.get("project", {})
                    if isinstance(project, dict) and project.get("test_command"):
                        return str(project["test_command"])
            except Exception:
                pass
        return None

    def _write_skip_control(self, state: State) -> None:
        """P10: Write .unison/control/skip.json to trigger orchestrator SKIP.

        The orchestrator's ``_check_control_files()`` reads and consumes
        this file at the next iteration boundary.
        """
        control_dir = self.world.root / ".unison" / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        skip_file = control_dir / "skip.json"
        skip_data = {
            "reason": (
                f"Observer detected {self._SKIP_CONSECUTIVE_THRESHOLD}+ "
                f"consecutive REQUEST_CHANGES in {state.phase} — "
                f"current output minimally satisfies requirements"
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": state.phase,
            "iteration": state.iteration,
        }
        skip_file.write_text(json.dumps(skip_data, indent=2, ensure_ascii=False))
        logger.warning("SKIP intervention: wrote %s", skip_file)

        # Emit intervention event
        lang = self.observer_language
        body = _msg("stalled", lang, elapsed=0, phase=state.phase)  # placeholder
        summary = (
            f"SKIP intervention after {self._SKIP_CONSECUTIVE_THRESHOLD}+ "
            f"REQUEST_CHANGES in {state.phase}"
        )
        self._emit_event(
            event_type="intervention",
            phase=state.phase,
            severity="warn",
            title=f"SKIP intervention: {state.phase}",
            body=summary,
            iteration=state.iteration,
            summary=summary,
        )

    # ---- structured event emission -------------------------------------------

    def _emit_event(
        self,
        event_type: str,
        phase: str = "",
        severity: str = "info",
        title: str = "",
        body: str = "",
        iteration: int = 0,
        verdict: str = "",
        summary: str = "",
    ) -> None:
        """P10: Emit a structured pipeline event to notifications.jsonl.

        Constructs a Notification with all structured fields and writes it
        via :meth:`_write_notification`.
        """
        notif = Notification(
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase=phase,
            severity=severity,  # type: ignore[arg-type]
            title=title,
            body=body,
            event_type=event_type,
            pipeline=self.pipeline_name,
            iteration=iteration,
            verdict=verdict,
            summary=summary,
            language=self.observer_language,
        )
        self._write_notification(notif)

    def _on_phase_event(self, event_data: dict) -> None:
        """Callback for event bus ``"phase"`` topic.

        Invoked from the publishing thread (orchestrator).  Pushes the
        event into the internal queue for processing by the main loop.
        This is intentionally non-blocking — the main loop drains the
        queue at its own pace.

        P10: Also emits structured pipeline events (pipeline_start,
        phase_done, pipeline_done, halted) directly — the event bus
        carries enough context that we don't need to wait for a
        state.json read.
        """
        try:
            self._event_queue.put_nowait(event_data)
        except queue.Full:
            pass  # drop event if queue is full (shouldn't happen)

        # ---- P10: Emit structured events from event bus data ----
        event_kind = event_data.get("event", "")
        phase = event_data.get("phase", "")
        iteration = event_data.get("iteration", 0)
        verdict = event_data.get("last_verdict", "")
        note = event_data.get("note", "")
        halt_signal = event_data.get("halt_signal", False)
        halt_reason = event_data.get("halt_reason", "")
        lang = self.observer_language

        if event_kind == "pipeline_start":
            agent_count = event_data.get("agent_count", 0)
            mode = event_data.get("mode", "")
            self._emit_event(
                event_type="pipeline_start",
                severity="info",
                title=_msg("pipeline_start", lang,
                          pipeline=self.pipeline_name, mode=mode,
                          agent_count=agent_count),
                body=f"Pipeline {self.pipeline_name} started in {mode} mode",
            )
        elif event_kind == "phase_done":
            commits = event_data.get("commits", 0)
            commits_detail = f" | {commits} commits" if commits else ""
            body = _msg("phase_done", lang,
                        phase=phase, iteration=iteration,
                        verdict=verdict or "PASS",
                        commits_detail=commits_detail)
            self._emit_event(
                event_type="phase_done",
                phase=phase,
                severity="info",
                title=body,
                body=body,
                iteration=iteration,
                verdict=verdict or "PASS",
                summary=body,
            )
        elif event_kind == "pipeline_done":
            commits = event_data.get("commits", 0)
            tests = event_data.get("tests", 0)
            self._emit_event(
                event_type="pipeline_done",
                severity="info",
                title=_msg("pipeline_done", lang,
                          pipeline=self.pipeline_name,
                          commits=commits, tests=tests),
                body=f"Pipeline {self.pipeline_name} complete",
            )
        elif event_kind == "halted":
            reason = halt_reason or "unknown"
            body = _msg("halted", lang,
                        reason=reason, phase=phase, iteration=iteration)
            self._emit_event(
                event_type="halted",
                phase=phase,
                severity="error",
                title=body,
                body=body,
                iteration=iteration,
                summary=body,
            )

    def _check_liveness_from_event(self, event_data: dict) -> bool:
        """Check liveness from an event bus event (no state.json read needed).

        The event carries ``halt_signal`` and ``phase``.  If the session
        is halted or in ``done`` phase, it's considered alive.  Otherwise
        we fall back to the file-based liveness check.

        Returns:
            True if the session appears alive.
        """
        phase = event_data.get("phase", "")
        halt_signal = event_data.get("halt_signal", False)

        # Done or halted phases are always "alive"
        if phase == "done" or halt_signal:
            return True

        # For active phases, trust the event — the orchestrator just
        # published it, so activity is recent.
        if phase in ("planning_active", "planning_review",
                      "dev_active", "dev_review", "init"):
            return True

        # Unknown phase — fall back to file-based check
        return self._check_liveness_from_file()

    def _check_liveness_from_file(self) -> bool:
        """Fallback: read state.json and run the standard liveness check."""
        if not self.world.state_file.exists():
            return False
        try:
            state = State.atomic_read(self.world.state_file)
            return self.check_liveness(state)
        except Exception:
            return False

    def check_liveness(self, state: State) -> bool:
        """5min 无活动 + phase ≠ done → False（紧急通知触发）。

        Returns:
            True if the session is alive (recent activity or done phase).
            False if stalled (no activity for > stall_threshold_seconds and not done).
        """
        # Done phase is always considered alive
        if state.phase == "done":
            return True

        # No activity timestamp → treat as stalled
        if state.last_activity is None:
            return False

        # Parse last_activity and compare to now
        try:
            last = datetime.strptime(
                state.last_activity, "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return False

        now = datetime.now(timezone.utc)
        elapsed = (now - last).total_seconds()

        return elapsed < self.stall_threshold_seconds

    def send_full_report(self, session_id: str, report_path: Path) -> bool:
        """Write a full report covering current state + recent notifications.

        Creates the parent directory if it doesn't exist.  Writes a
        timestamped summary of the current state and any unread
        notification lines.

        Args:
            session_id: Target Hermes session ID.
            report_path: Path to the report file to write.

        Returns:
            True if the report was written successfully.
        """
        report_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        lines.append(f"# Observer Report\n")
        lines.append(f"Session: {session_id}\n")
        lines.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n")
        lines.append(f"\n")

        # Include current state summary
        if self.world.state_file.exists():
            try:
                state = State.atomic_read(self.world.state_file)
                lines.append(f"## State\n")
                lines.append(f"- Phase: {state.phase}\n")
                lines.append(f"- Iteration: {state.iteration}\n")
                lines.append(f"- Last activity: {state.last_activity}\n")
                lines.append(f"- Halt signal: {state.halt_signal}\n")
                if state.halt_reason:
                    lines.append(f"- Halt reason: {state.halt_reason}\n")
                lines.append(f"\n")
            except Exception:
                lines.append(f"## State\n")
                lines.append(f"(could not read state.json)\n")
                lines.append(f"\n")

        # Include recent notifications
        if self.world.notifications_file.exists():
            try:
                content = self.world.notifications_file.read_text(encoding="utf-8")
                notif_lines = [l for l in content.strip().split("\n") if l.strip()]
                if notif_lines:
                    lines.append(f"## Notifications ({len(notif_lines)} total)\n")
                    for nl in notif_lines[-20:]:  # Last 20 notifications
                        lines.append(f"- {nl}\n")
                else:
                    lines.append(f"## Notifications\n")
                    lines.append(f"(none)\n")
            except OSError:
                lines.append(f"## Notifications\n")
                lines.append(f"(could not read notifications.jsonl)\n")

        report_path.write_text("".join(lines), encoding="utf-8")
        return True

    # ---- private -------------------------------------------------------------

    def _write_notification(self, notif: Notification) -> None:
        """追加一条 JSONL 通知到 notifications.jsonl。

        Args:
            notif: The Notification to write.
        """
        self.world.observer_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "timestamp": notif.timestamp,
            "phase": notif.phase,
            "severity": notif.severity,
            "title": notif.title,
            "body": notif.body,
            "event_type": notif.event_type,
            "pipeline": notif.pipeline,
            "iteration": notif.iteration,
            "verdict": notif.verdict,
            "summary": notif.summary,
            "language": notif.language,
        }

        with open(self.world.notifications_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # === Discord webhook push (if configured) ===
        webhook_url = os.environ.get("UNISON_DISCORD_WEBHOOK", "")
        if webhook_url and notif.severity in ("error", "warn", "info"):
            try:
                self._push_discord(webhook_url, notif)
            except Exception as exc:
                logger.warning("Discord push failed: %s", exc)

    def _push_discord(self, webhook_url: str, notif: Notification) -> None:
        """Send a compact Discord notification via webhook. Non-blocking."""
        color_map = {"error": 0xFF0000, "warn": 0xFFA500, "info": 0x3498DB}
        color = color_map.get(notif.severity, 0x808080)

        embed = {
            "title": notif.title[:256],
            "description": notif.body[:1024],
            "color": color,
            "timestamp": notif.timestamp,
            "fields": [
                {"name": "Phase", "value": notif.phase, "inline": True},
                {"name": "Severity", "value": notif.severity, "inline": True},
            ],
        }

        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)

    def _create_default_watcher(self):
        """根据平台选择 watcher。

        Returns:
            InotifyWatcher (Linux) 或 PollingWatcher (fallback)。
        """
        if sys.platform == "linux":
            try:
                return InotifyWatcher()
            except OSError as e:
                errno_val = getattr(e, "errno", None)
                if errno_val == errno.ENOSPC:
                    logger.warning(
                        "inotify watches exhausted, falling back to polling",
                    )
                else:
                    logger.warning(
                        "inotify unavailable (%s), falling back to polling", e,
                    )
                return PollingWatcher(interval_seconds=5)
        else:
            return PollingWatcher(interval_seconds=5)

    def _full_rescan(self) -> None:
        """全量重新扫描 state.json + notifications.jsonl。

        当 inotify 队列满（overflow 事件）时调用。
        """
        # 检查 state.json
        if self.world.state_file.exists():
            state = State.atomic_read(self.world.state_file)
            if not self.check_liveness(state):
                lang = self.observer_language
                body = _msg("stalled", lang,
                            elapsed=self.stall_threshold_seconds,
                            phase=state.phase)
                self._emit_event(
                    event_type="stalled",
                    phase=state.phase,
                    severity="warn",
                    title=body,
                    body=body,
                    summary=body,
                )

        # 检查 notifications.jsonl
        if self.world.notifications_file.exists():
            self._process_new_notifications()

    def _process_new_notifications(self) -> None:
        """处理 notifications.jsonl 中新增的通知行。

        按文件偏移跟踪已处理内容，仅在新内容到达时写一次 report
        文件。这保证每个状态变更只触发一次全量报告。
        """
        if not self.world.notifications_file.exists():
            return

        try:
            current_size = self.world.notifications_file.stat().st_size
        except OSError:
            return

        if current_size <= self._notification_offset:
            return  # No new content since last read

        # Read only new content from the tracked offset
        try:
            with open(self.world.notifications_file, "r", encoding="utf-8") as f:
                f.seek(self._notification_offset)
                _new_data = f.read()
        except OSError:
            return

        self._notification_offset = current_size

        # Write a full report on each new state change
        report_path = self.world.report_file(1)
        self.send_full_report("observer", report_path)
