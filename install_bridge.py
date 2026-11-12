"""Install the DaVinci Bot bridge into Resolve's Workspace > Scripts menu (macOS and Windows).

Free Resolve only lists Lua scripts there. Run from the repo root:  uv run python install_bridge.py
Re-run it whenever the repo folder moves (the bridge stores the repo path).
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from davinci_bot.bridge import IPC_DIR  # noqa: E402  (same IPC folder the MCP server uses)

if sys.platform == "win32":
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    dest = appdata / "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility"
elif sys.platform == "darwin":
    dest = Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
else:
    sys.exit("unsupported platform: " + sys.platform)

dest.mkdir(parents=True, exist_ok=True)
IPC_DIR.mkdir(parents=True, exist_ok=True)
src = (HERE / "resolve_script/DaVinci Bot Bridge.lua").read_text(encoding="utf-8")
# forward slashes: Lua dofile accepts them on Windows and they need no escaping inside a Lua string
out = src.replace("__PROJECT_DIR__", HERE.as_posix()).replace("__IPC_DIR__", IPC_DIR.as_posix())
target = dest / "DaVinci Bot Bridge.lua"
target.write_text(out, encoding="utf-8")
print(f"Installed: {target}")
print(f"IPC dir:   {IPC_DIR}")
