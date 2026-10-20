from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Condition
from typing import Literal


ChunkKind = Literal["init", "media", "reset"]


@dataclass(frozen=True, slots=True)
class StreamChunk:
    kind: ChunkKind
    data: bytes = b""


class Subscription:
    def __init__(
        self,
        max_queue: int = 16,
        client_id: str | None = None,
        transport: str = "",
        user_agent: str = "",
        remote_port: int = 0,
    ) -> None:
        self.client_id = client_id
        self.transport = transport
        self.user_agent = user_agent
        self.remote_port = remote_port
        self.connected_at = time.monotonic()
        self.last_activity = self.connected_at
        self.bytes_sent = 0
        self.queue: Queue[StreamChunk] = Queue(maxsize=max_queue)

    def get(self, timeout: float | None = None) -> StreamChunk | None:
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None


class StreamHub:
    def __init__(self, history_size: int = 2) -> None:
        self._condition = Condition()
        self._buffer = bytearray()
        self._init_buffer = bytearray()
        self._fragment_buffer = bytearray()
        self._init_segment: bytes | None = None
        self._history: deque[bytes] = deque(maxlen=max(1, history_size))
        self._subscriptions: set[Subscription] = set()

    @property
    def client_count(self) -> int:
        with self._condition:
            client_ids = {subscription.client_id for subscription in self._subscriptions if subscription.client_id}
            anonymous_count = sum(subscription.client_id is None for subscription in self._subscriptions)
            return len(client_ids) + anonymous_count

    @property
    def ready(self) -> bool:
        with self._condition:
            return self._init_segment is not None

    def subscribe(
        self,
        client_id: str | None = None,
        transport: str = "",
        user_agent: str = "",
        remote_port: int = 0,
    ) -> Subscription:
        subscription = Subscription(
            client_id=client_id,
            transport=transport,
            user_agent=user_agent,
            remote_port=remote_port,
        )
        with self._condition:
            self._subscriptions.add(subscription)
            if self._init_segment is not None:
                self._offer(subscription, StreamChunk("init", self._init_segment))
                for fragment in self._history:
                    self._offer(subscription, StreamChunk("media", fragment))
            self._condition.notify_all()
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._condition:
            self._subscriptions.discard(subscription)
            self._condition.notify_all()

    def mark_sent(self, subscription: Subscription, size: int) -> None:
        if size <= 0:
            return
        with self._condition:
            subscription.bytes_sent += size
            subscription.last_activity = time.monotonic()
            self._condition.notify_all()

    def connections(self) -> list[dict[str, object]]:
        now = time.monotonic()
        with self._condition:
            return [
                {
                    "id": subscription.client_id or "",
                    "transport": subscription.transport,
                    "host": subscription.client_id or "",
                    "port": subscription.remote_port,
                    "user_agent": subscription.user_agent,
                    "connected_at": round(subscription.connected_at, 3),
                    "connected_for": round(now - subscription.connected_at, 1),
                    "idle_for": round(now - subscription.last_activity, 1),
                    "bytes_sent": subscription.bytes_sent,
                }
                for subscription in self._subscriptions
            ]

    def reset(self) -> None:
        with self._condition:
            self._buffer.clear()
            self._init_buffer.clear()
            self._fragment_buffer.clear()
            self._init_segment = None
            self._history.clear()
            for subscription in self._subscriptions:
                self._replace(subscription, StreamChunk("reset"))
            self._condition.notify_all()

    def feed(self, data: bytes) -> None:
        if not data:
            return
        with self._condition:
            self._buffer.extend(data)
            try:
                self._drain_boxes()
            except ValueError:
                self._buffer.clear()
                self._init_buffer.clear()
                self._fragment_buffer.clear()
            self._condition.notify_all()

    def _drain_boxes(self) -> None:
        while True:
            box = self._read_box()
            if box is None:
                return
            kind, data = box
            if self._init_segment is None:
                self._init_buffer.extend(data)
                if kind == b"moov":
                    self._init_segment = bytes(self._init_buffer)
                    self._init_buffer.clear()
                    for subscription in self._subscriptions:
                        self._offer(subscription, StreamChunk("init", self._init_segment))
                continue
            if kind == b"moof":
                self._fragment_buffer.clear()
                self._fragment_buffer.extend(data)
            elif kind == b"mdat":
                if self._fragment_buffer:
                    self._fragment_buffer.extend(data)
                    fragment = bytes(self._fragment_buffer)
                    self._fragment_buffer.clear()
                else:
                    fragment = data
                self._history.append(fragment)
                for subscription in self._subscriptions:
                    self._offer(subscription, StreamChunk("media", fragment))

    def _read_box(self) -> tuple[bytes, bytes] | None:
        if len(self._buffer) < 8:
            return None
        size = int.from_bytes(self._buffer[:4], "big")
        kind = bytes(self._buffer[4:8])
        header_size = 8
        if size == 1:
            if len(self._buffer) < 16:
                return None
            size = int.from_bytes(self._buffer[8:16], "big")
            header_size = 16
        elif size == 0:
            size = len(self._buffer)
        if size < header_size or size > 512 * 1024 * 1024:
            raise ValueError("invalid MP4 box size")
        if len(self._buffer) < size:
            return None
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return kind, data

    @staticmethod
    def _offer(subscription: Subscription, chunk: StreamChunk) -> None:
        try:
            subscription.queue.put_nowait(chunk)
        except Full:
            try:
                subscription.queue.get_nowait()
            except Empty:
                pass
            try:
                subscription.queue.put_nowait(chunk)
            except Full:
                pass

    @staticmethod
    def _replace(subscription: Subscription, chunk: StreamChunk) -> None:
        while True:
            try:
                subscription.queue.get_nowait()
            except Empty:
                break
        try:
            subscription.queue.put_nowait(chunk)
        except Full:
            pass
