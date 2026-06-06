"""Local HTTP file server for shared assets between Agnes nodes.

Why this exists
---------------
The Agnes image and video APIs only accept PUBLIC URLs for image-to-image
and image-to-video inputs. We previously relied on free public upload
hosts (0x0.st, uguu.se, tmpfiles.org) but those are unreliable —
0x0.st disabled uploads entirely in 2026.

This server provides a stable, locally-managed alternative:
  1. The image node writes its output to a managed directory
  2. It then returns a URL pointing at THIS server (127.0.0.1:8765)
  3. Downstream nodes can use the URL without any external dependency

Features
--------
  - Single-file dependency (stdlib only, no Flask/uvicorn needed)
  - Listens on 127.0.0.1 (loopback) — no external network exposure
  - Auto-cleans files older than MAX_AGE_HOURS
  - MIME-type inference from extension
  - Threaded so ComfyUI requests don't block

Usage
-----
  python3 agnes_share_server.py [--port 8765] [--root /path/to/agnes_share]
  python3 agnes_share_server.py --foreground  # don't daemonize

Then in your custom node:
  url = f"http://127.0.0.1:8765/{relpath}"
"""
from __future__ import annotations

import argparse
import http.server
import mimetypes
import os
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

# Where shared files live. Override with --root.
DEFAULT_ROOT = os.environ.get(
    "AGNES_SHARE_ROOT",
    os.path.expanduser("~/Documents/ComfyUI/agnes_share"),
)

# Auto-cleanup: files older than this many hours get deleted on every
# serve request. Set to 0 to disable.
MAX_AGE_HOURS = int(os.environ.get("AGNES_SHARE_MAX_AGE_HOURS", "24"))


def cleanup_old_files(root: Path, max_age_hours: int) -> int:
    """Delete files in root older than max_age_hours. Returns count deleted."""
    if max_age_hours <= 0:
        return 0
    cutoff = time.time() - max_age_hours * 3600
    deleted = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            pass
    # Also remove empty subdirectories
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return deleted


class ShareHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that:
      - Serves files only from the configured root (security: no path traversal)
      - Cleans up old files on each request
      - Returns proper MIME types
    """

    # Silence the default per-request stderr noise
    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(
            f"[agnes_share] {self.address_string()} {format % args}\n"
        )

    def do_GET(self) -> None:
        # Strip leading slash, normalize, reject path traversal
        rel = self.path.lstrip("/")
        if ".." in rel.split("/") or rel.startswith("~"):
            self.send_error(403, "forbidden")
            return

        # Periodic cleanup (cheap, ~microseconds when nothing to delete)
        if MAX_AGE_HOURS > 0 and time.time() - self.server.last_cleanup > 300:
            # at most once per 5 minutes
            try:
                cleanup_old_files(self.server.root, MAX_AGE_HOURS)
                self.server.last_cleanup = time.time()
            except Exception:
                pass

        target = (self.server.root / rel).resolve()
        # Make sure target is still under root
        try:
            target.relative_to(self.server.root.resolve())
        except ValueError:
            self.send_error(403, "forbidden")
            return

        if not target.is_file():
            self.send_error(404, f"not found: {rel}")
            return

        # Send file
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = "application/octet-stream"
        try:
            data = target.read_bytes()
        except OSError as e:
            self.send_error(500, f"read error: {e}")
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        # Same logic as GET but send only headers (no body).
        # This is what `requests.head()` and CDN preflight checks use.
        rel = self.path.lstrip("/")
        if ".." in rel.split("/") or rel.startswith("~"):
            self.send_error(403)
            return
        target = (self.server.root / rel).resolve()
        try:
            target.relative_to(self.server.root.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle each request in a new thread."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, root: Path):
        super().__init__(addr, handler)
        self.root = root
        self.last_cleanup = 0.0


def is_port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=8765,
        help="TCP port to listen on (default 8765)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Interface to bind (default loopback only — do NOT use 0.0.0.0)",
    )
    parser.add_argument(
        "--root", default=DEFAULT_ROOT,
        help=f"Directory to serve (default: {DEFAULT_ROOT})",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    if not is_port_free(args.host, args.port):
        print(
            f"[agnes_share] port {args.port} already in use on {args.host}; "
            f"another instance is probably already serving {root}",
            file=sys.stderr,
        )
        return 1

    server = ThreadedHTTPServer((args.host, args.port), ShareHTTPHandler, root)
    print(
        f"[agnes_share] serving {root} on http://{args.host}:{args.port}/",
        file=sys.stderr,
    )
    print(
        f"[agnes_share] auto-cleanup: files older than {MAX_AGE_HOURS}h "
        f"are deleted every 5 min",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[agnes_share] shutting down", file=sys.stderr)
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
