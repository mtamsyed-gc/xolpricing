"""XoL page server entry (0.9.1): runs the vendored GC UI kit server with the XoL manifest on this tool folder.

    py ui\\xol\\serve.py [--port 8765] [--no-browser]        (tools\\xol_ui.bat calls this)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "kit"))
import gc_server  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--manifest" not in argv:
        argv = ["--manifest", str(HERE / "manifest.json")] + argv
    if "--client" not in argv:
        argv += ["--client", str(HERE.parents[1])]
    if "--port" not in argv:
        argv += ["--port", "8765"]
    gc_server.main(argv)
