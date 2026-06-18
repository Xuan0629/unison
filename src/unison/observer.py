"""observer.py — Observer: FileWatcher + liveness + notification dual-write."""

from __future__ import annotations

import ctypes
import errno
import json
import logging
import os
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from unison.world import World
from unison.state import State

logger = logging.getLogger(__name__)


# ============================================================================
# Notification
# ============================================================================


@dataclass
class Notification:
    """Observer 输出的事件。"""

    timestamp: str
    phase: str
    severity: Literal["info", "warn", "error"]
    title: str
    body: str


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
                        "inotify_add_watch(%s): %s — degrading",
                        watch_dir, os.strerror(err),
                    )
                    self._running = False
                    return
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
        watcher: InotifyWatcher | PollingWatcher | MockWatcher | None = None,
    ) -> None:
        """
        Args:
            world: 项目工作区布局。
            stall_threshold_seconds: 停滞阈值（秒）。超过此时间无活动视为 stalled。
            watcher: 文件监控器。None 则根据平台自动选择。
        """
        self.world = world
        self.stall_threshold_seconds = stall_threshold_seconds
        self.watcher = watcher if watcher is not None else self._create_default_watcher()
        self._running = False

    # ---- public API ----------------------------------------------------------

    def run(self) -> None:
        """阻塞循环。监控 state.json + notifications.jsonl。Ctrl-C 退出。

        监控父目录而非文件，按文件名过滤（避免 atomic_write inode 陷阱）。

        Raises:
            RuntimeError: state.json 缺失时。
        """
        # 确保目录存在
        self.world.ensure_directories()

        # 监控两个父目录
        paths = [self.world.unison_dir, self.world.observer_dir]
        self.watcher.watch(paths)

        self._running = True
        while self._running:
            event = self.watcher.next_event(timeout_seconds=1.0)

            if event is None:
                continue

            # 处理 overflow 事件（inotify 队列满）
            if event.event_type == "overflow":
                self._full_rescan()
                continue

            # 过滤非目标文件事件
            if event.path.name not in ("state.json", "notifications.jsonl"):
                continue

            # 处理 state.json 变化
            if event.path.name == "state.json":
                if not self.world.state_file.exists():
                    raise RuntimeError("state.json missing, cannot continue")

                state = State.atomic_read(self.world.state_file)
                if not self.check_liveness(state):
                    self._write_notification(Notification(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        phase=state.phase,
                        severity="warn",
                        title="Session stalled",
                        body=(
                            f"No activity for {self.stall_threshold_seconds}s+ "
                            f"in phase {state.phase}"
                        ),
                    ))

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

        self.watcher.stop()

    def stop(self) -> None:
        """停止 Observer 循环。"""
        self._running = False
        self.watcher.stop()

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
        """全量报告发到启动器会话（仅当 --from-hermes-session 时）。

        Args:
            session_id: Target Hermes session ID.
            report_path: Path to the report file to send.

        Returns:
            True if the report was sent successfully.
        """
        # Stub implementation — the real send would use Hermes send_message.
        if not report_path.exists():
            return False
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
        }

        with open(self.world.notifications_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

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
                self._write_notification(Notification(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    phase=state.phase,
                    severity="warn",
                    title="Session stalled (post-overflow rescan)",
                    body=(
                        f"No activity for {self.stall_threshold_seconds}s+ "
                        f"in phase {state.phase}"
                    ),
                ))

        # 检查 notifications.jsonl
        if self.world.notifications_file.exists():
            self._process_new_notifications()

    def _process_new_notifications(self) -> None:
        """处理 notifications.jsonl 中新增的通知行。

        读取并转发到 Discord（stub — 实际发送未实现）。
        """
        # Stub: 在未来的 V1.2 中实现 Discord webhook 发送。
        # 当前仅确保日志文件存在且可读。
        try:
            if self.world.notifications_file.exists():
                _ = self.world.notifications_file.read_text()
        except OSError:
            pass
