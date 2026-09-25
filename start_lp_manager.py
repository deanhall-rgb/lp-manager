from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Start LP Manager v0.8.11 Stabilisation Fixes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if not args.no_browser and args.host in {"127.0.0.1", "localhost"}:
        def open_browser():
            time.sleep(1.0)
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run("lp_manager.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
