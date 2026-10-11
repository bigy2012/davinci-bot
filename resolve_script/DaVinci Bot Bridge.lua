-- DaVinci Bot Bridge
-- Run from DaVinci Resolve: Workspace > Scripts > DaVinci Bot Bridge
--
-- Free Resolve blocks external scripting and runs menu scripts in a sandboxed Lua
-- (no io, no sockets, no Python). What it does allow:
--   * dofile(path)                -> we read requests written by the MCP server as Lua files
--   * fusion:SavePrefs(path)      -> writes the prefs tree (incl. our reply key) to a file at once
-- (printerr also reaches ResolveDebug.txt, but that log is only flushed every ~10s.)
-- Operations live in resolve_script/ops.lua and are reloaded for every request.

local PROJECT_DIR = "__PROJECT_DIR__"
local IPC_DIR = "__IPC_DIR__"
local REPLY_KEY = "Global.DVBot.Reply"
local BEAT_KEY = "Global.DVBot.Beat"
local INSTANCE_KEY = "Global.DVBot.Instance"

local function req_path(n) return IPC_DIR .. "/req_" .. n .. ".lua" end

local function load_counter()
  local ok, n = pcall(dofile, IPC_DIR .. "/counter.lua")
  if ok and type(n) == "number" then return n end
  return 1
end

-- reply file holds Reply = "<seq>|<hex payload>" (hex keeps Fusion's serializer from mangling JSON)
local function reply(id, payload)
  local hex = payload:gsub(".", function(c) return string.format("%02x", c:byte()) end)
  fusion:SetPrefs(REPLY_KEY, id .. "|" .. hex)
  fusion:SavePrefs(IPC_DIR .. "/reply.prefs")
  fusion:SetPrefs(REPLY_KEY, "")  -- keep big replies out of the user's Fusion.prefs
end

-- beat.prefs tells the client the bridge is alive: "idle|<time>" every second, "busy|<seq>|<op>" during work
local function beat(state)
  fusion:SetPrefs(BEAT_KEY, state)
  fusion:SavePrefs(IPC_DIR .. "/beat.prefs")
end

-- only one bridge may run: a newly started one takes over and older loops exit
math.randomseed(os.time() + math.floor(os.clock() * 1000))
local my_id = tostring(os.time()) .. "-" .. tostring(math.random(1, 1e9))
fusion:SetPrefs(INSTANCE_KEY, my_id)

local res = resolve or bmd.scriptapp("Resolve")
local seq = load_counter()
-- a request written before the bridge started is still pending
if seq > 1 and fileexists(req_path(seq - 1)) then seq = seq - 1 end

reply("ready", '{"seq":' .. seq .. '}')

local running = true
local last_beat = 0
while running do
  if fusion:GetPrefs(INSTANCE_KEY) ~= my_id then break end
  local path = req_path(seq)
  if fileexists(path) then
    fusion:SetPrefs(REPLY_KEY, seq .. "|ack")
    fusion:SavePrefs(IPC_DIR .. "/reply.prefs")
    beat("busy|" .. seq .. "|" .. os.time())
    local okm, ops = pcall(dofile, PROJECT_DIR .. "/resolve_script/ops.lua")
    local okr, req = pcall(dofile, path)
    local out
    if not okm then
      local msg = tostring(ops):gsub('[%c"\\]', " ")
      out = '{"ok":false,"error":"ops.lua failed to load: ' .. msg .. '"}'
    elseif not okr or type(req) ~= "table" then
      out = ops.encode({ ok = false, error = "bad request file: " .. tostring(req) })
    elseif req.op == "__stop__" then
      out = ops.encode({ ok = true, result = "stopped" })
      running = false
    else
      out = ops.dispatch(res, req.op, req.args or {})
    end
    reply(seq, out)
    seq = seq + 1
    last_beat = 0
  else
    if os.time() - last_beat >= 1 then
      beat("idle|" .. os.time())
      last_beat = os.time()
    end
    bmd.wait(0.05)
  end
end
fusion:SetPrefs(REPLY_KEY, "")
fusion:SavePrefs()
