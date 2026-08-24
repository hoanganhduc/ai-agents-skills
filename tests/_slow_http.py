from __future__ import annotations

import contextlib
import http.server
import threading
import time


@contextlib.contextmanager
def slow_http_server(
    *,
    body: bytes = b"x" * 100,
    drip_seconds: float = 0.1,
    drip_headers: bool = False,
    content_length: int | None = None,
):
    """Serve one loopback response slowly enough to evade an idle timeout."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _send(self):
            try:
                declared_length = len(body) if content_length is None else content_length
                if drip_headers:
                    header = (
                        b"HTTP/1.1 200 OK\r\n"
                        + f"Content-Length: {declared_length}\r\n".encode("ascii")
                        + b"Content-Type: application/json\r\n"
                        + b"Connection: close\r\n\r\n"
                    )
                    for byte in header:
                        self.connection.sendall(bytes((byte,)))
                        time.sleep(drip_seconds)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(declared_length))
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                for byte in body:
                    self.connection.sendall(bytes((byte,)))
                    time.sleep(drip_seconds)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        do_GET = _send
        do_POST = _send

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/slow"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@contextlib.contextmanager
def mixed_status_http_server():
    """Serve fixed 404, truncated, and healthy loopback responses."""

    hits = {"bad": 0, "truncate": 0, "healthy": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path.startswith("/bad/"):
                hits["bad"] += 1
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if self.path.startswith("/truncate/"):
                hits["truncate"] += 1
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                # Advertise a three-byte chunk but close after two bytes. The
                # stdlib raises nested IncompleteRead exceptions whose partial
                # buffers together describe the consumed response bytes.
                self.wfile.write(b"3\r\nxy")
                self.wfile.flush()
                self.close_connection = True
                return
            hits["healthy"] += 1
            body = b"<rss/>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
