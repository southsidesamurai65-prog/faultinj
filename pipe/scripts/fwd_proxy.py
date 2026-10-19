#!/usr/bin/env python3
"""TCP forwarder so Docker containers can reach the host HTTP proxy.

Containers cannot reach the internet directly in this environment; they can
reach the host via the docker bridge gateway. This forwards
0.0.0.0:19829 -> 127.0.0.1:19828 (the host proxy from ~/proxy.sh).

Start detached:
    setsid python3 scripts/fwd_proxy.py </dev/null >>/tmp/fwd_proxy.log 2>&1 &
"""

import socket
import sys
import threading

LISTEN = ("0.0.0.0", 19829)
TARGET = ("127.0.0.1", 19828)


def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(128)
    sys.stderr.write(f"forwarding {LISTEN} -> {TARGET}\n")
    sys.stderr.flush()
    while True:
        conn, _ = srv.accept()
        try:
            upstream = socket.create_connection(TARGET)
        except OSError:
            conn.close()
            continue
        threading.Thread(target=pipe, args=(conn, upstream), daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, conn), daemon=True).start()


if __name__ == "__main__":
    main()
