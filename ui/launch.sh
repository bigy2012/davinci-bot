#!/bin/zsh
# Start the DaVinci Bot web UI (if not running) and open it in the default browser.
HERE="${0:A:h:h}"
PORT="${DAVINCI_BOT_PORT:-8765}"
URL="http://127.0.0.1:$PORT/"
LOG="$HOME/Library/Logs/davinci-bot-ui.log"
UV="$HOME/.local/bin/uv"
if ! /usr/bin/curl -s -o /dev/null "$URL"; then
  cd "$HERE" && nohup "$UV" run --directory "$HERE" python ui/app.py >> "$LOG" 2>&1 &
  for i in {1..60}; do /usr/bin/curl -s -o /dev/null "$URL" && break; sleep 0.25; done
fi
/usr/bin/open "$URL"
