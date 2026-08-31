import socket
import threading

from NoorSuite.protocol import frame, read_frame


def test_frame_read_frame_roundtrip():
    a, b = socket.socketpair()
    try:
        payload = {"action": "append_trace", "name": "t", "x": [1, 2, 3], "nested": {"k": [0.5]}}
        a.sendall(frame(payload))
        assert read_frame(b) == payload
    finally:
        a.close()
        b.close()


def test_read_frame_reassembles_across_chunks():
    a, b = socket.socketpair()
    try:
        payload = {"blob": list(range(5000))}
        raw = frame(payload)

        def dribble():
            for i in range(0, len(raw), 64):
                a.sendall(raw[i:i + 64])
        t = threading.Thread(target=dribble)
        t.start()
        assert read_frame(b) == payload
        t.join()
    finally:
        a.close()
        b.close()


def test_read_frame_returns_none_on_closed_socket():
    a, b = socket.socketpair()
    a.close()
    try:
        assert read_frame(b) is None
    finally:
        b.close()
