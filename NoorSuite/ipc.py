"""The GUI-side IPC server.

Wire format and the Qt-free client live in :mod:`NoorSuite.protocol`; this module
adds the server ``QObject`` that runs inside the GUI process.

Mutations are handed to the GUI thread through the :attr:`IPCBridge.data_received`
Qt signal (fire-and-forget).  Read-only queries are answered directly from
:attr:`IPCBridge.snapshot`, a plain dict the GUI thread rewrites after every
repository change -- this avoids blocking cross-thread calls.
"""
from __future__ import annotations

import socket
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from .protocol import (ACTION_ADD_IMAGE_TO_SHEET, ACTION_ADD_TO_SHEET,
                       ACTION_APPEND_DATAFRAME, ACTION_APPEND_IMAGE,
                       ACTION_APPEND_TRACE, ACTION_CLEAR, ACTION_GET_DATA,
                       ACTION_GET_IMAGE, ACTION_LIST_DATA, ACTION_LIST_IMAGES,
                       ACTION_LIST_SHEETS, ACTION_LIST_TRACES, ACTION_PING,
                       ACTION_REMOVE_DATA, ACTION_REMOVE_TRACE, DEFAULT_PORT,
                       MUTATION_ACTIONS, QUERY_ACTIONS, IPCClient, frame, read_frame)

__all__ = ["IPCBridge", "IPCClient", "frame", "read_frame", "DEFAULT_PORT",
           "ACTION_PING", "ACTION_APPEND_TRACE", "ACTION_APPEND_DATAFRAME",
           "ACTION_APPEND_IMAGE", "ACTION_ADD_TO_SHEET", "ACTION_ADD_IMAGE_TO_SHEET",
           "ACTION_LIST_DATA", "ACTION_LIST_IMAGES", "ACTION_LIST_TRACES",
           "ACTION_LIST_SHEETS", "ACTION_GET_DATA", "ACTION_GET_IMAGE",
           "ACTION_REMOVE_DATA", "ACTION_REMOVE_TRACE", "ACTION_CLEAR",
           "MUTATION_ACTIONS", "QUERY_ACTIONS"]


class IPCBridge(QObject):
    """TCP server that runs inside the GUI process."""

    data_received = pyqtSignal(dict)

    def __init__(self, port: int = DEFAULT_PORT):
        super().__init__()
        self.port = port
        self.server_socket = None
        self.running = False
        # Read-only view of the repository, refreshed by the GUI thread.
        self.snapshot = {"data_objects": [], "images": [], "traces": [], "sheets": [],
                         "data_full": {}}

    def start(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind(("127.0.0.1", self.port))
            self.server_socket.listen(5)
        except OSError:
            print(f"[IPC] Port {self.port} already bound. A GUI instance may already exist.")
            return
        self.running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop(self) -> None:
        self.running = False
        try:
            if self.server_socket:
                self.server_socket.close()
        except OSError:
            pass

    def _listen_loop(self) -> None:
        # One thread per connection: `accept()` must return immediately for the next
        # client, so a single slow/stuck one (a kernel that connected but is slow to
        # send, or whose socket just stalls) can't hold up every other kernel's calls
        # behind it -- previously this handled connections one at a time in this same
        # loop, so any single stuck `read_frame`/`sendall` blocked *all* IPC traffic,
        # including from other Jupyter kernels, until that one call finally gave up.
        while self.running:
            try:
                conn, _ = self.server_socket.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn) -> None:
        try:
            conn.settimeout(10.0)   # never let one client wedge its own handler thread
            payload = read_frame(conn)
            if payload is not None:
                conn.sendall(frame(self._handle(payload)))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle(self, payload: dict) -> dict:
        action = payload.get("action")
        if action == ACTION_PING:
            return {"status": "alive"}
        if action == ACTION_LIST_DATA:
            return {"status": "success", "data": self.snapshot.get("data_objects", [])}
        if action == ACTION_LIST_IMAGES:
            return {"status": "success", "images": self.snapshot.get("images", [])}
        if action == ACTION_LIST_TRACES:
            return {"status": "success", "traces": self.snapshot.get("traces", [])}
        if action == ACTION_LIST_SHEETS:
            return {"status": "success", "sheets": self.snapshot.get("sheets", [])}
        if action == ACTION_GET_DATA:
            key = payload.get("key")
            for entry in self.snapshot.get("data_full", {}).values():
                if key in (entry.get("id"), entry.get("name")):
                    return {"status": "success", **entry}
            return {"status": "error", "message": f"no data object matching {key!r}"}
        if action == ACTION_GET_IMAGE:
            key = payload.get("key")
            for entry in self.snapshot.get("image_full", {}).values():
                if key in (entry.get("id"), entry.get("name")):
                    return {"status": "success", **entry}
            return {"status": "error", "message": f"no image matching {key!r}"}
        # Anything else is a mutation -> hand it to the GUI thread.
        self.data_received.emit(payload)
        return {"status": "success"}
