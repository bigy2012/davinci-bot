-- Operations executed INSIDE DaVinci Resolve by the bridge (sandboxed Lua 5.1).
-- Every op takes (res, args) and returns a JSON-encodable Lua value. Times are seconds.

local M = {}

-- ---------- JSON ----------

local function is_array(t)
  local n = 0
  for k in pairs(t) do
    if type(k) ~= "number" then return false end
    n = n + 1
  end
  return n == #t
end

local function encode(v)
  local t = type(v)
  if v == nil then return "null"
  elseif t == "boolean" then return tostring(v)
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end
    return string.format("%.6g", v)
  elseif t == "string" then
    return '"' .. v:gsub('[%c"\\]', function(c)
      local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
      return map[c] or string.format("\\u%04x", c:byte())
    end) .. '"'
  elseif t == "table" then
    local parts = {}
    if next(v) == nil then return "[]" end
    if is_array(v) then
      for i = 1, #v do parts[i] = encode(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, val in pairs(v) do parts[#parts + 1] = encode(tostring(k)) .. ":" .. encode(val) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return encode(tostring(v))
end
M.encode = encode

-- ---------- helpers ----------

-- Resolve returns lists as tables keyed 1..n plus a "__flags" entry; return an ordered array
local function list(t)
  local keys, out = {}, {}
  for k in pairs(t or {}) do
    if type(k) == "number" then keys[#keys + 1] = k end
  end
  table.sort(keys)
  for _, k in ipairs(keys) do out[#out + 1] = t[k] end
  return out
end

local function round(x, n)
  local m = 10 ^ (n or 3)
  return math.floor(x * m + 0.5) / m
end

local function project(res)
  local p = res:GetProjectManager():GetCurrentProject()
  if not p then error("no project open in Resolve") end
  return p
end

local function timeline_names(p)
  local names = {}
  for i = 1, p:GetTimelineCount() do names[#names + 1] = p:GetTimelineByIndex(i):GetName() end
  return names
end

local function get_timeline(res, name)
  local p = project(res)
  if name then
    for i = 1, p:GetTimelineCount() do
      local tl = p:GetTimelineByIndex(i)
      if tl:GetName() == name then
        p:SetCurrentTimeline(tl)
        return tl
      end
    end
    error("timeline not found: " .. name)
  end
  local tl = p:GetCurrentTimeline()
  if not tl then error("no current timeline") end
  return tl
end

local function tl_fps(tl) return tonumber(tl:GetSetting("timelineFrameRate")) or 30 end

local function clip_fps(clip) return tonumber(clip:GetClipProperty("FPS")) or 0 end

local function walk(folder, acc)
  acc = acc or {}
  for _, c in ipairs(list(folder:GetClipList())) do acc[#acc + 1] = c end
  for _, sub in ipairs(list(folder:GetSubFolderList())) do walk(sub, acc) end
  return acc
end

local function find_by_path(mp, path)
  for _, c in ipairs(walk(mp:GetRootFolder())) do
    if c:GetClipProperty("File Path") == path then return c end
  end
end

local function get_bin(mp, name)
  local root = mp:GetRootFolder()
  if not name then return root end
  for _, sub in ipairs(list(root:GetSubFolderList())) do
    if sub:GetName() == name then return sub end
  end
  return mp:AddSubFolder(root, name)
end

local function ensure_media(res, path, bin_name)
  local mp = project(res):GetMediaPool()
  local c = find_by_path(mp, path)
  if c then return c end
  if not fileexists(path) then error("file not found: " .. path) end
  local prev = mp:GetCurrentFolder()
  mp:SetCurrentFolder(get_bin(mp, bin_name))
  local items = list(mp:ImportMedia({ path }))
  if prev then mp:SetCurrentFolder(prev) end
  if not items[1] then error("Resolve could not import: " .. path) end
  return items[1]
end

local function clip_info(c)
  return {
    name = c:GetName(), path = c:GetClipProperty("File Path"), fps = clip_fps(c),
    duration = c:GetClipProperty("Duration"), resolution = c:GetClipProperty("Resolution"),
    type = c:GetClipProperty("Type"),
  }
end

local function item_info(it, fps, start)
  return { name = it:GetName(), start = round((it:GetStart() - start) / fps), ["end"] = round((it:GetEnd() - start) / fps) }
end

local function ensure_track(tl, kind, index)
  while tl:GetTrackCount(kind) < index do
    if not tl:AddTrack(kind) then break end
  end
end

-- ---------- ops ----------

local ops = {}

function ops.ping(res)
  local p = res:GetProjectManager():GetCurrentProject()
  local tl = p and p:GetCurrentTimeline()
  return {
    product = res:GetProductName(), version = res:GetVersionString(),
    project = p and p:GetName(), timeline = tl and tl:GetName(),
  }
end

function ops.list_projects(res)
  return list(res:GetProjectManager():GetProjectListInCurrentFolder())
end

function ops.open_project(res, a)
  local pm = res:GetProjectManager()
  local p = pm:LoadProject(a.name)
  if not p and a.create_if_missing then p = pm:CreateProject(a.name) end
  if not p then error("cannot open or create project: " .. a.name) end
  return ops.ping(res)
end

function ops.save_project(res)
  return res:GetProjectManager():SaveProject()
end

function ops.list_media(res)
  local out = {}
  for _, c in ipairs(walk(project(res):GetMediaPool():GetRootFolder())) do out[#out + 1] = clip_info(c) end
  return out
end

function ops.import_media(res, a)
  local out = {}
  for _, p in ipairs(a.paths) do out[#out + 1] = clip_info(ensure_media(res, p, a.bin_name)) end
  return out
end

function ops.list_timelines(res)
  return timeline_names(project(res))
end

function ops.timeline_info(res, a)
  local tl = get_timeline(res, a.timeline)
  local fps, start = tl_fps(tl), tl:GetStartFrame()
  local tracks = {}
  for _, kind in ipairs({ "video", "audio", "subtitle" }) do
    for i = 1, tl:GetTrackCount(kind) do
      local items = {}
      for _, it in ipairs(list(tl:GetItemListInTrack(kind, i))) do items[#items + 1] = item_info(it, fps, start) end
      tracks[kind:sub(1, 1):upper() .. i] = items
    end
  end
  return {
    name = tl:GetName(), fps = fps,
    width = tl:GetSetting("timelineResolutionWidth"), height = tl:GetSetting("timelineResolutionHeight"),
    duration = round((tl:GetEndFrame() - start) / fps), tracks = tracks,
  }
end

-- segments: { {path, start, end}, ... } source seconds, in timeline order
function ops.build_timeline(res, a)
  local p = project(res)
  local mp = p:GetMediaPool()
  for _, n in ipairs(timeline_names(p)) do
    if n == a.name then error("timeline already exists: " .. a.name .. " (choose another name)") end
  end
  local clips = {}
  for i, seg in ipairs(a.segments) do clips[i] = ensure_media(res, seg.path, a.bin_name) end
  -- a project's frame rate can only change before its first timeline: match the footage
  if p:GetTimelineCount() == 0 then
    local fps = a.fps or clip_fps(clips[1])
    if fps and fps > 0 then p:SetSetting("timelineFrameRate", tostring(fps)) end
  end
  local tl = mp:CreateEmptyTimeline(a.name)
  if not tl then error("could not create timeline") end
  p:SetCurrentTimeline(tl)
  if a.width and a.height then
    tl:SetSetting("useCustomSettings", "1")
    tl:SetSetting("timelineResolutionWidth", tostring(math.floor(a.width)))
    tl:SetSetting("timelineResolutionHeight", tostring(math.floor(a.height)))
  end
  local infos = {}
  for i, seg in ipairs(a.segments) do
    local info = { mediaPoolItem = clips[i] }
    if seg.start or seg["end"] then
      local fps = clip_fps(clips[i])
      if fps == 0 then fps = tl_fps(tl) end
      local s = math.floor((seg.start or 0) * fps + 0.5)
      info.startFrame = s
      if seg["end"] then info.endFrame = math.max(s + 1, math.floor(seg["end"] * fps + 0.5)) end
    end
    infos[#infos + 1] = info
  end
  local placed = list(mp:AppendToTimeline(infos))
  if #placed == 0 then error("AppendToTimeline failed") end
  return ops.timeline_info(res, { timeline = a.name })
end

-- Frame-exact rough cut with picture and sound placed separately (allows J/L cuts).
-- video/audio: { {path, src_in, src_out, record}, ... } in frames; record is relative to timeline start.
-- zooms: optional per-video-item zoom (punch-ins); audio_fade_frames: crossfade at audio joins.
function ops.build_av_timeline(res, a)
  local p = project(res)
  local mp = p:GetMediaPool()
  for _, n in ipairs(timeline_names(p)) do
    if n == a.name then error("timeline already exists: " .. a.name .. " (choose another name)") end
  end
  local clips = {}
  local function clip_for(path)
    if not clips[path] then clips[path] = ensure_media(res, path, a.bin_name) end
    return clips[path]
  end
  for _, v in ipairs(a.video) do clip_for(v.path) end
  for _, v in ipairs(a.audio) do clip_for(v.path) end
  if p:GetTimelineCount() == 0 and a.fps then p:SetSetting("timelineFrameRate", tostring(a.fps)) end
  local tl = mp:CreateEmptyTimeline(a.name)
  if not tl then error("could not create timeline") end
  p:SetCurrentTimeline(tl)
  if a.width and a.height then
    tl:SetSetting("useCustomSettings", "1")
    tl:SetSetting("timelineResolutionWidth", tostring(math.floor(a.width)))
    tl:SetSetting("timelineResolutionHeight", tostring(math.floor(a.height)))
  end
  local start = tl:GetStartFrame()
  -- layout frames are in source fps; convert record positions if the timeline runs at another rate
  local scale = (a.fps and a.fps > 0) and (tl_fps(tl) / a.fps) or 1
  if math.abs(scale - 1) < 0.002 then scale = 1 end
  local function place(items, media_type)
    local infos = {}
    for _, it in ipairs(items) do
      infos[#infos + 1] = {
        mediaPoolItem = clip_for(it.path), mediaType = media_type, trackIndex = 1,
        startFrame = it.src_in, endFrame = it.src_out,
        recordFrame = start + math.floor(it.record * scale + 0.5),
      }
    end
    return list(mp:AppendToTimeline(infos))
  end
  local vids = place(a.video, 1)
  local auds = place(a.audio, 2)
  local zoomed, faded = 0, 0
  for i, z in ipairs(a.zooms or {}) do
    if vids[i] and z and z ~= 1 then
      vids[i]:SetProperty("ZoomX", z); vids[i]:SetProperty("ZoomY", z)
      zoomed = zoomed + 1
    end
  end
  if a.audio_fade_frames and a.audio_fade_frames > 0 then
    for i = 1, #auds - 1 do
      local ok = auds[i]:AddTransition({ type = "Cross Fade 0 dB", category = "audio", position = "end",
                                         alignment = "center", duration = a.audio_fade_frames })
      if ok then faded = faded + 1 end
    end
  end
  local info = ops.timeline_info(res, { timeline = a.name })
  info.placed = { video = #vids, audio = #auds, zoomed = zoomed, audio_fades = faded }
  return info
end

-- items: { {path, at (timeline sec), duration, src_start}, ... } video only, on an upper track
function ops.add_broll(res, a)
  local tl = get_timeline(res, a.timeline)
  local mp = project(res):GetMediaPool()
  local fps, start = tl_fps(tl), tl:GetStartFrame()
  local track = a.track or 2
  ensure_track(tl, "video", track)
  local placed = {}
  for _, it in ipairs(a.items) do
    local clip = ensure_media(res, it.path, a.bin_name or "B-roll")
    local cfps = clip_fps(clip)
    if cfps == 0 then cfps = fps end
    local s = math.floor((it.src_start or 0) * cfps + 0.5)
    local info = {
      mediaPoolItem = clip, mediaType = 1, trackIndex = track,
      recordFrame = start + math.floor(it.at * fps + 0.5), startFrame = s,
    }
    if it.duration then
      info.endFrame = s + math.max(1, math.floor(it.duration * cfps + 0.5))
    elseif clip:GetClipProperty("Type") == "Still" then
      info.endFrame = s + math.floor(3 * cfps + 0.5)
    end
    for _, o in ipairs(list(mp:AppendToTimeline({ info }))) do placed[#placed + 1] = item_info(o, fps, start) end
  end
  return { placed = placed, timeline = ops.timeline_info(res, { timeline = tl:GetName() }) }
end

local function find_track(tl, name)
  for i = 1, tl:GetTrackCount("video") do
    if tl:GetTrackName("video", i) == name then return i end
  end
end

-- copy of a track's clips, so they can be re-laid on another track
local function snapshot_track(tl, idx)
  local snap = {}
  for _, it in ipairs(list(tl:GetItemListInTrack("video", idx))) do
    local mpi = it:GetMediaPoolItem()
    if mpi then
      local off = math.floor(it:GetLeftOffset() + 0.5)
      snap[#snap + 1] = { mpi = mpi, start = it:GetStart(), src_in = off,
                          src_out = off + math.floor(it:GetDuration() + 0.5) }
    end
  end
  return snap
end

local function clear_track(tl, idx)
  local old = list(tl:GetItemListInTrack("video", idx))
  if #old > 0 and tl:DeleteClips(old, false) then return #old end
  return 0
end

local function restore_track(mp, idx, snap)
  local infos = {}
  for _, c in ipairs(snap) do
    infos[#infos + 1] = { mediaPoolItem = c.mpi, mediaType = 1, trackIndex = idx, recordFrame = c.start,
                          startFrame = c.src_in, endFrame = c.src_out }
  end
  if #infos > 0 then mp:AppendToTimeline(infos) end
end

-- Return the index of the video track called `name`, creating it if needed. Tracks named in
-- `keep_above` (e.g. Captions) must stay above it: if one would end up below, its clips move up a track.
local function ensure_named_track(tl, mp, name, keep_above)
  local idx = find_track(tl, name)
  if idx then return idx end
  tl:AddTrack("video")
  local top = tl:GetTrackCount("video")
  local lowest
  for _, other in ipairs(keep_above or {}) do
    local o = find_track(tl, other)
    if o and o < top and (not lowest or o < lowest) then lowest = o end
  end
  if not lowest then
    tl:SetTrackName("video", top, name)
    return top
  end
  -- shift every track from `lowest` up by one, top-down, then take over `lowest`
  for i = top - 1, lowest, -1 do
    local snap = snapshot_track(tl, i)
    local nm = tl:GetTrackName("video", i)
    clear_track(tl, i + 1)
    restore_track(mp, i + 1, snap)
    tl:SetTrackName("video", i + 1, nm)
  end
  clear_track(tl, lowest)
  tl:SetTrackName("video", lowest, name)
  return lowest
end

-- Place rendered clips (captions, B-roll) on the video track named a.track_name; re-running replaces it.
-- items: { {path, record_frame, frames}, ... }; a.keep_above: track names that must stay on top
function ops.add_overlays(res, a)
  local tl = get_timeline(res, a.timeline)
  local mp = project(res):GetMediaPool()
  local name = a.track_name or "Captions"
  local track = ensure_named_track(tl, mp, name, a.keep_above)
  local removed = clear_track(tl, track)
  local start = tl:GetStartFrame()
  local infos = {}
  for _, it in ipairs(a.items) do
    infos[#infos + 1] = {
      mediaPoolItem = ensure_media(res, it.path, a.bin_name or name), mediaType = 1,
      trackIndex = track, recordFrame = start + it.record_frame, startFrame = 0, endFrame = it.frames,
    }
  end
  local placed = list(mp:AppendToTimeline(infos))
  return { track = track, track_name = name, placed = #placed, replaced = removed }
end

local function ensure_audio_track(tl, name)
  for i = 1, tl:GetTrackCount("audio") do
    if tl:GetTrackName("audio", i) == name then return i end
  end
  tl:AddTrack("audio", "stereo")
  local idx = tl:GetTrackCount("audio")
  tl:SetTrackName("audio", idx, name)
  return idx
end

-- Music / SFX on named audio tracks (re-running replaces those tracks).
-- tracks: { {name, items = { {path, at, src_in, src_out, volume_db, fade_in, fade_out}, ... }}, ... }  (seconds)
function ops.place_audio(res, a)
  local tl = get_timeline(res, a.timeline)
  local mp = project(res):GetMediaPool()
  local fps, start = tl_fps(tl), tl:GetStartFrame()
  local report = {}
  for _, tr in ipairs(a.tracks) do
    local idx = ensure_audio_track(tl, tr.name)
    local old = list(tl:GetItemListInTrack("audio", idx))
    if #old > 0 then tl:DeleteClips(old, false) end
    local placed, failed = 0, 0
    for _, it in ipairs(tr.items or {}) do
      local clip = ensure_media(res, it.path, a.bin_name or "Audio")
      local cf = clip_fps(clip)
      if cf == 0 then cf = fps end
      local s = math.floor(it.src_in * cf + 0.5)
      local out = list(mp:AppendToTimeline({ {
        mediaPoolItem = clip, mediaType = 2, trackIndex = idx,
        recordFrame = start + math.floor(it.at * fps + 0.5),
        startFrame = s, endFrame = math.max(s + 1, math.floor(it.src_out * cf + 0.5)),
      } }))
      local item = out[1]
      if item then
        placed = placed + 1
        if it.volume_db then
          item:SetProperty("AudioVolumeEnabled", true)
          item:SetProperty("AudioVolume", math.max(-100, math.min(30, it.volume_db)))
        end
        if (it.fade_in or 0) > 0 or (it.fade_out or 0) > 0 then
          item:SetFades({ FadeIn = math.floor((it.fade_in or 0) * fps + 0.5),
                          FadeOut = math.floor((it.fade_out or 0) * fps + 0.5) })
        end
      else
        failed = failed + 1
      end
    end
    report[#report + 1] = { track = tr.name, index = idx, placed = placed, failed = failed, replaced = #old }
  end
  return report
end

-- ---------- grading ----------

local function frames_to_tc(frames, fps)
  local nominal = math.floor(fps + 0.5)
  local f = frames % nominal
  local s = math.floor(frames / nominal)
  return string.format("%02d:%02d:%02d:%02d", math.floor(s / 3600), math.floor(s / 60) % 60, s % 60, f)
end

function ops.goto_time(res, a)
  local tl = get_timeline(res, a.timeline)
  local tc = frames_to_tc(tl:GetStartFrame() + math.floor(a.at * tl_fps(tl) + 0.5), tl_fps(tl))
  return { ok = tl:SetCurrentTimecode(tc), timecode = tl:GetCurrentTimecode() }
end

-- current frame (with its grade) as an image file
function ops.export_frame(res, a)
  if a.at then ops.goto_time(res, a) end
  return project(res):ExportCurrentFrameAsStill(a.path)
end

-- grab the current clip's grade into the gallery and export it as a .drx preset
function ops.save_grade(res, a)
  local tl = get_timeline(res, a.timeline)
  if a.at then ops.goto_time(res, a) end
  local still = tl:GrabStill()
  if not still then error("GrabStill failed (open the Color page on a clip)") end
  local album = project(res):GetGallery():GetCurrentStillAlbum()
  local ok = album:ExportStills({ still }, a.folder, a.name, "drx")
  return { exported = ok, folder = a.folder, name = a.name }
end

-- apply a .drx grade to clips on a video track (all, or the 1-based indexes in a.items)
function ops.apply_grade(res, a)
  local tl = get_timeline(res, a.timeline)
  local items = list(tl:GetItemListInTrack("video", a.track or 1))
  local pick = {}
  for _, i in ipairs(a.items or {}) do pick[i] = true end
  local done, failed = 0, 0
  for i, it in ipairs(items) do
    if not a.items or pick[i] then
      local g = it:GetNodeGraph()
      if g and g:ApplyGradeFromDRX(a.path, a.grade_mode or 0) then done = done + 1 else failed = failed + 1 end
    end
  end
  return { applied = done, failed = failed, clips = #items }
end

-- put a .cube LUT on node a.node (default 1) of clips on a video track
function ops.apply_lut(res, a)
  local tl = get_timeline(res, a.timeline)
  project(res):RefreshLUTList()
  local items = list(tl:GetItemListInTrack("video", a.track or 1))
  local pick = {}
  for _, i in ipairs(a.items or {}) do pick[i] = true end
  local done, failed = 0, 0
  for i, it in ipairs(items) do
    if not a.items or pick[i] then
      local g = it:GetNodeGraph()
      if g and g:SetLUT(a.node or 1, a.path) then done = done + 1 else failed = failed + 1 end
    end
  end
  return { applied = done, failed = failed, clips = #items }
end

function ops.reset_grade(res, a)
  local tl = get_timeline(res, a.timeline)
  local n = 0
  for _, it in ipairs(list(tl:GetItemListInTrack("video", a.track or 1))) do
    local g = it:GetNodeGraph()
    if g and g:ResetAllGrades() then n = n + 1 end
  end
  return { reset = n }
end

function ops.set_clip_transform(res, a)
  local tl = get_timeline(res, a.timeline)
  local n = 0
  for _, it in ipairs(list(tl:GetItemListInTrack("video", a.track or 2))) do
    if a.zoom then it:SetProperty("ZoomX", a.zoom); it:SetProperty("ZoomY", a.zoom) end
    if a.pan then it:SetProperty("Pan", a.pan) end
    if a.tilt then it:SetProperty("Tilt", a.tilt) end
    n = n + 1
  end
  return { updated = n }
end

function ops.add_transitions(res, a)
  local tl = get_timeline(res, a.timeline)
  local items = list(tl:GetItemListInTrack("video", a.track or 1))
  local done = 0
  for i = 1, #items - 1 do
    local opts = { type = a.kind or "Cross Dissolve", category = a.category or "simple",
                   position = "end", alignment = "center", duration = a.frames or 12 }
    if items[i]:AddTransition(opts) then
      done = done + 1
    end
  end
  return { added = done, cuts = math.max(0, #items - 1) }
end

function ops.add_markers(res, a)
  local tl = get_timeline(res, a.timeline)
  local fps, ok = tl_fps(tl), 0
  for _, m in ipairs(a.markers) do
    local note = m.note or ""
    if tl:AddMarker(math.floor(m.at * fps + 0.5), m.color or "Blue", note:sub(1, 60), note, 1) then ok = ok + 1 end
  end
  return { added = ok }
end

function ops.import_subtitles(res, a)
  local tl = get_timeline(res, a.timeline)
  local mp = project(res):GetMediaPool()
  local clip = ensure_media(res, a.srt_path, "Subtitles")
  ensure_track(tl, "subtitle", 1)
  local out = list(mp:AppendToTimeline({ { mediaPoolItem = clip, recordFrame = tl:GetStartFrame() } }))
  if #out == 0 then
    error("could not place subtitles - drag " .. a.srt_path .. " from the Subtitles bin onto the timeline")
  end
  return { subtitle_items = #list(tl:GetItemListInTrack("subtitle", 1)) }
end

function ops.open_page(res, a)
  return res:OpenPage(a.page)
end

function ops.list_render_presets(res)
  return list(project(res):GetRenderPresetList())
end

function ops.render(res, a)
  local p = project(res)
  local tl = get_timeline(res, a.timeline)
  if a.preset and not p:LoadRenderPreset(a.preset) then error("render preset not found: " .. a.preset) end
  p:SetRenderSettings({ SelectAllFrames = true, TargetDir = a.target_dir, CustomName = a.filename })
  local job = p:AddRenderJob()
  if not job or job == "" then error("could not add render job (check Deliver page settings)") end
  if a.start then p:StartRendering({ job }) end
  return { job = job, timeline = tl:GetName(), started = a.start and true or false }
end

function ops.render_status(res, a)
  local p = project(res)
  return { rendering = p:IsRenderingInProgress(), status = p:GetRenderJobStatus(a.job) }
end

function M.dispatch(res, op, args)
  local fn = ops[op]
  if not fn then return encode({ ok = false, error = "unknown op: " .. tostring(op) }) end
  local ok, result = pcall(fn, res, args)
  if not ok then return encode({ ok = false, error = tostring(result) }) end
  return encode({ ok = true, result = result })
end

return M
