#!/bin/zsh
# Install the bridge into Resolve's Workspace > Scripts menu (free Resolve only lists Lua scripts).
set -e
HERE="${0:A:h}"
IPC="$HOME/Library/Application Support/davinci-bot/ipc"
DEST="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
mkdir -p "$DEST" "$IPC"
sed -e "s|__PROJECT_DIR__|$HERE|" -e "s|__IPC_DIR__|$IPC|" \
  "$HERE/resolve_script/DaVinci Bot Bridge.lua" > "$DEST/DaVinci Bot Bridge.lua"
echo "Installed: $DEST/DaVinci Bot Bridge.lua"
