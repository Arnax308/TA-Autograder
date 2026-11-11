"""
Root GUI launcher for C/C++ TA Autograder.
Starts local web server and automatically opens browser dashboard.
"""

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

ROOT_DIR = Path(__file__).resolve().parent


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_browser(port: int = 8765):
    time.sleep(1.2)
    url = f"http://127.0.0.1:{port}"
    print(f"\n=======================================================")
    print(f"  C/C++ TA Autograder Dashboard running at: {url}")
    print(f"=======================================================\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    # Relative folders (assignments/, reports/, results/, drivers/) are resolved from the project root,
    # so the dashboard works no matter which directory it is launched from.
    os.chdir(ROOT_DIR)
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

    port = int(os.getenv("TA_GUI_PORT", "8765"))
    if _port_in_use(port):
        print(f"Port {port} is already in use - the dashboard may already be running. Opening it in the browser.")
        webbrowser.open(f"http://127.0.0.1:{port}")
        return

    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    uvicorn.run("grader.server:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
