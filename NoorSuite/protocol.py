"""Qt-free wire protocol: framing helpers, action names, and the low-level client.

Kept separate from :mod:`NoorSuite.ipc` (which needs PyQt6 for the server ``QObject``)
so the Jupyter client and the tests can import it without a Qt install.
"""
from __future__ import annotations

import pickle
import socket

DEFAULT_PORT = 55555
_LEN_BYTES = 8

# --- protocol actions -----------------------------------------------------------
ACTION_PING = "ping"
ACTION_APPEND_TRACE = "append_trace"
ACTION_APPEND_DATAFRAME = "append_dataframe"
ACTION_APPEND_IMAGE = "append_image"
ACTION_ADD_TO_SHEET = "add_to_sheet"
ACTION_ADD_IMAGE_TO_SHEET = "add_image_to_sheet"
ACTION_LIST_DATA = "list_data"
ACTION_LIST_IMAGES = "list_images"
ACTION_LIST_TRACES = "list_traces"
ACTION_GET_DATA = "get_data"
ACTION_GET_IMAGE = "get_image"
ACTION_REMOVE_DATA = "remove_data"
ACTION_REMOVE_TRACE = "remove_trace"   # alias of remove_data
ACTION_CLEAR = "clear"

MUTATION_ACTIONS = {ACTION_APPEND_TRACE, ACTION_APPEND_DATAFRAME, ACTION_APPEND_IMAGE,
                    ACTION_ADD_TO_SHEET, ACTION_ADD_IMAGE_TO_SHEET, ACTION_REMOVE_DATA,
                    ACTION_REMOVE_TRACE, ACTION_CLEAR}
QUERY_ACTIONS = {ACTION_LIST_DATA, ACTION_LIST_IMAGES, ACTION_LIST_TRACES,
                 ACTION_GET_DATA, ACTION_GET_IMAGE}


def frame(payload: dict) -> bytes:
    body = pickle.dumps(payload)
    return len(body).to_bytes(_LEN_BYTES, "big") + body


def _recv_exactly(conn, n: int):
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(min(8192, n - len(buf)))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def read_frame(conn):
    raw_len = _recv_exactly(conn, _LEN_BYTES)
    if raw_len is None:
        return None
    body = _recv_exactly(conn, int.from_bytes(raw_len, "big"))
    if body is None:
        return None
    return pickle.loads(body)


class IPCClient:
    """Low-level request/response helper used by :class:`~NoorSuite.client.SciSuiteClient`."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port

    def is_alive(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", self.port))
            s.sendall(frame({"action": ACTION_PING}))
            resp = read_frame(s)
            s.close()
            return bool(resp) and resp.get("status") == "alive"
        except OSError:
            return False

    def send(self, payload: dict, timeout: float = 5.0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", self.port))
        try:
            s.sendall(frame(payload))
            return read_frame(s)
        finally:
            s.close()
