from __future__ import annotations

import struct
import unittest

from desktop_streamer.hub import StreamHub


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack("!I4s", 8 + len(payload), kind) + payload


class StreamHubTests(unittest.TestCase):
    def test_split_init_and_media(self) -> None:
        hub = StreamHub(history_size=1)
        subscription = hub.subscribe()
        init = box(b"ftyp", b"isom") + box(b"moov", b"init")
        hub.feed(init[:7])
        self.assertIsNone(subscription.get(0.01))
        hub.feed(init[7:])
        first = subscription.get(0.1)
        assert first is not None
        self.assertEqual(first.kind, "init")
        media = box(b"moof", b"header") + box(b"mdat", b"frame")
        hub.feed(media[:5])
        self.assertIsNone(subscription.get(0.01))
        hub.feed(media[5:])
        chunk = subscription.get(0.1)
        assert chunk is not None
        self.assertEqual(chunk.kind, "media")
        self.assertEqual(chunk.data, media)
        hub.unsubscribe(subscription)

    def test_late_subscriber_gets_init_and_history(self) -> None:
        hub = StreamHub(history_size=1)
        hub.feed(box(b"ftyp") + box(b"moov", b"init"))
        media = box(b"moof") + box(b"mdat", b"frame")
        hub.feed(media)
        subscription = hub.subscribe()
        first = subscription.get(0.1)
        second = subscription.get(0.1)
        assert first is not None
        assert second is not None
        self.assertEqual(first.kind, "init")
        self.assertEqual(second.data, media)
        hub.unsubscribe(subscription)

    def test_reset_notifies_existing_subscriber(self) -> None:
        hub = StreamHub()
        subscription = hub.subscribe()
        hub.reset()
        chunk = subscription.get(0.1)
        assert chunk is not None
        self.assertEqual(chunk.kind, "reset")
        hub.unsubscribe(subscription)


if __name__ == "__main__":
    unittest.main()
