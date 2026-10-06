#!/usr/bin/env python3
"""Serve public/ on 127.0.0.1 so the guide can be opened or checked without the
orchestrator. `--stop` ends a server this script started and is safe to run twice.

  scripts/serve-guide.py            # http://127.0.0.1:18281/guide/
  scripts/serve-guide.py --stop
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 18281
PIDFILE = ROOT / "artifacts" / "serve-guide.pid"


def stop() -> int:
    if not PIDFILE.is_file():
        return 0

    try:
        os.kill(int(PIDFILE.read_text().strip()), signal.SIGTERM)
    except (ProcessLookupError, ValueError):
        pass

    PIDFILE.unlink(missing_ok=True)

    return 0


def serve() -> int:
    PIDFILE.parent.mkdir(exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "public")
    )

    try:
        with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as server:
            print(f"serving http://127.0.0.1:{PORT}/guide/", file=sys.stderr, flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        PIDFILE.unlink(missing_ok=True)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stop", action="store_true")

    return stop() if parser.parse_args().stop else serve()


if __name__ == "__main__":
    raise SystemExit(main())
