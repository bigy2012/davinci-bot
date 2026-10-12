# DaVinci Bot

MCP server ที่ให้ Claude ตัดต่อใน **DaVinci Resolve เวอร์ชันฟรี** ตามคำสั่งของคุณ ใช้ได้กับงานตัดคลิปยาวเป็นคลิปสั้น, เรียงช็อตตามสคริปต์, ใส่ B-roll, ทำซับไทย และ export

## ใช้งานทุกครั้ง

1. เปิด DaVinci Resolve แล้วเปิดโปรเจกต์ที่จะทำงาน
2. ไปที่เมนู **Workspace → Scripts → DaVinci Bot Bridge**
   (ต้องกดใหม่ทุกครั้งที่เปิด Resolve ส่วนตอนสลับโปรเจกต์ไม่ต้องกดใหม่)
3. สั่ง Claude ได้เลย เช่น "ตัดคลิปสั้นจาก /path/video.mp4" แล้ว Claude จะถามรายละเอียดงานก่อนเริ่มทุกครั้ง

## หน้าเว็บ / แอป Mac

ดับเบิลคลิก **`~/Applications/DaVinci Bot.app`** (ลากไปไว้ที่ Dock ได้) หรือรัน `ui/launch.sh` จะเปิดหน้าเว็บ `http://127.0.0.1:8765` ในเบราว์เซอร์
- แชทสั่งงานภาษาไทย ใช้ Claude ผ่าน Agent SDK (บัญชีเดียวกับ Claude Code) พร้อม skill ทั้ง 6 ฟังก์ชัน
- มีการ์ดฟังก์ชันพร้อมฟอร์ม, แถบสถานะ Resolve และรายชื่อ timeline แบบสด, แสดงภาพ เสียง และไฟล์ตัวอย่างในหน้าเลย
- คำสั่งที่เสี่ยง (Bash, เขียนไฟล์นอก Agent-M/reports, ดาวน์โหลด B-roll) จะขึ้นกล่องให้กดอนุญาตก่อนทุกครั้ง
- **ประวัติแชท** (แท็บ "ประวัติแชท"): ค้นหา, เปลี่ยนชื่อ, ลบ, เปิดแชทเก่าแล้วคุยต่อได้ด้วยบริบทเดิม และวางเมาส์บนข้อความที่เคยส่งแล้วกด "✎ แก้ไขแล้วส่งใหม่" จะแตกเป็นแชทใหม่จากจุดนั้นโดยแชทเดิมยังอยู่ (แสดงเฉพาะแชทที่เริ่มจากหน้าเว็บ ซึ่งติดแท็ก `davinci-ui` ส่วนแชท Claude Code ในโฟลเดอร์เดียวกันจะไม่ปนมา)
- ฟังเฉพาะ 127.0.0.1 และทุก API ต้องใช้ token ที่สุ่มใหม่ทุกครั้งที่เปิดแอป เว็บไซต์อื่นจึงสั่งงานแทนไม่ได้
- log อยู่ที่ `~/Library/Logs/davinci-bot-ui.log` ปิดแอปด้วยปุ่ม "ปิดแอป" บนหน้าเว็บ
- ไฟล์: `ui/app.py` (เซิร์ฟเวอร์), `ui/index.html` (หน้าเว็บ), `ui/launch.sh` (ตัวเปิด)

## ฟังก์ชัน (skills)

skill อยู่ในโฟลเดอร์ `skills/` และลิงก์ไว้ที่ `~/.claude/skills/` แก้กฎใน `SKILL.md` ได้โดยตรง

| # | Skill | ทำอะไร |
|---|---|---|
| 1 | `davinci-rough-cut` | ลัพคัท: ตัดช่วงเงียบ/คำซ้ำ/พูดผิด เลือกเทคที่ดีที่สุด ต่อให้เป็นเรื่องเดียว (jump, punch-in, J, L cut) |
| 2 | `davinci-subtitles` | ซับไทยตามบริบท: คลิปยาวกลางล่างจอ, คลิปสั้นมีสีสัน เด้ง ไฮไลต์คำ (pop / karaoke / box) |
| 3 | `davinci-broll` | B-roll/insert อัตโนมัติ: หาภาพ ฟุตเทจ ภาพเก่า มีม จากแหล่งที่บอกลิขสิทธิ์ เตรียม (Ken Burns, PiP, meme) แล้ววางบน track B-roll พร้อมเครดิต |
| 4 | `davinci-audio` | เพลงประกอบ + SFX จากคลังของคุณ: หาจังหวะตบมุก/กระแทกอารมณ์/ลุ้น, ให้เพลงพุ่งตรงจุดหักมุม, ducking ใต้เสียงพูดอัตโนมัติ |
| 5 | `davinci-look` | preset แต่งหน้าใส `natural-skin` (.drx: ผิวเนียน+ใส) และ LUT `natural-skin-tone` 3 ระดับ (.cube: ผิวใส ใช้ได้ทั้ง Resolve และ CapCut) |
| 6 | `davinci-creator-analysis` | วิเคราะห์ creator จากงานจริง (ฟุตเทจ สคริปต์ แบรนด์) หาเสน่ห์ จุดเด่น จุดด้อย แนวทางต่อ ออกเป็นรายงาน .md ใน `reports/` |
| 7 | `davinci-script` | เขียนสคริปต์ Agent-M แนว Digital & Audio Forensics จาก input ตาราง หมวดหมู่ / หัวข้อ / การเปรียบเทียบ (มีช่วง DEMO หน้ากล้อง, Ticking Clock, Visual Metaphors, เอกสารทางการ) ด้วยกฎของ skill `action-story-channel-script` + teleprompter |

## ขั้นตอนการทำงานของ Claude

1. **ถาม brief** เช่น แนวตั้งหรือแนวนอน, ความยาว, สคริปต์, สไตล์และโฟลเดอร์ B-roll, ซับ, transition, ชื่อ timeline
2. **วิเคราะห์ไฟล์** ด้วยการถอดเสียง (Whisper large-v3 ในเครื่อง) และดึงภาพนิ่งมาดูเนื้อหาช็อต
3. **เสนอแผนการตัดเป็นตาราง** แล้วรอคุณอนุมัติหรือแก้ไขก่อน
4. **ตัดจริง** ลงใน timeline ใหม่เสมอ งานเดิมจะไม่ถูกเขียนทับ

## เครื่องมือ

| กลุ่ม | เครื่องมือ |
|---|---|
| สถานะ | `resolve_status`, `list_projects`, `open_project`, `save_project` |
| วิเคราะห์ไฟล์ | `probe_media`, `list_media_folder`, `transcribe`, `export_frame_previews` |
| Rough cut | `analyze_speech`, `refine_speech`, `preview_rough_cut`, `build_rough_cut` |
| ตัดต่อ | `import_media`, `build_timeline`, `add_broll`, `set_clip_transform`, `add_transitions`, `add_markers` |
| ซับไตเติล | `draft_captions`, `preview_captions`, `render_captions` |
| B-roll | `search_broll`, `fetch_broll`, `preview_broll`, `place_broll` |
| เพลง + SFX | `audio_library`, `sfx_cue_map`, `preview_audio_mix`, `place_audio` |
| Look / แต่งหน้า | `list_looks`, `preview_look`, `apply_look`, `reset_look` |
| วิเคราะห์ creator / โค้ช | `creator_footage_metrics`, `script_digest`, `practice_feedback` |
| สคริปต์ | `make_prompter`, `official_doc` (ค้น/อ่าน/ไฮไลต์หน้าเอกสารทางการ) + skill เขียนสคริปต์ของคุณ |
| ดูงาน | `list_media_pool`, `list_timelines`, `timeline_info`, `open_page` |
| Export | `list_render_presets`, `render`, `render_status` |

Prompt `edit_brief` คือเช็คลิสต์ที่ Claude ใช้ถามก่อนเริ่มงาน

## วิธีทำงานเบื้องหลัง

Resolve เวอร์ชันฟรีปิดการเชื่อมต่อจากโปรแกรมภายนอก และให้รันในเมนูได้เฉพาะ Lua แบบจำกัดสิทธิ์ (อ่านเขียนไฟล์และเปิด socket ไม่ได้) bridge จึงสื่อสารผ่านไฟล์แบบนี้:

```
Claude ─ MCP (server.py) ─ เขียน req_N.lua ──▶ Lua bridge ใน Resolve (dofile)
                        ◀── อ่าน reply.prefs ── fusion:SavePrefs(path)
```

ไฟล์ IPC อยู่ที่ `~/Library/Application Support/davinci-bot/ipc/` ใช้เวลาตอบกลับประมาณ 0.05 วินาทีต่อคำสั่ง

- `resolve_script/DaVinci Bot Bridge.lua` คือลูปรับคำสั่ง (ติดตั้งด้วย `./install_bridge.sh`)
- `resolve_script/ops.lua` คือคำสั่งตัดต่อทั้งหมด ซึ่งโหลดใหม่ทุกคำสั่ง แก้ไฟล์แล้วไม่ต้องรีสตาร์ท bridge
- `davinci_bot/bridge.py` คือฝั่ง client
- `davinci_bot/media.py` จัดการ ffprobe, Whisper และไฟล์ SRT
- `server.py` คือ MCP server

## ติดตั้งใหม่ / ย้ายเครื่อง

```bash
./install_bridge.sh
uv sync
claude mcp add --scope user davinci-bot -- uv run --directory "$PWD" server.py
ln -s "$PWD/skills/davinci-rough-cut" ~/.claude/skills/davinci-rough-cut
ln -s "$PWD/skills/davinci-subtitles" ~/.claude/skills/davinci-subtitles
ln -s "$PWD/skills/davinci-broll" ~/.claude/skills/davinci-broll
ln -s "$PWD/skills/davinci-audio" ~/.claude/skills/davinci-audio
ln -s "$PWD/skills/davinci-look" ~/.claude/skills/davinci-look
ln -s "$PWD/skills/davinci-creator-analysis" ~/.claude/skills/davinci-creator-analysis
ln -s "$PWD/skills/davinci-script" ~/.claude/skills/davinci-script
# แอป Mac (เลือกทำ)
osacompile -o ~/Applications/DaVinci\ Bot.app -e 'do shell script quoted form of "'"$PWD"'/ui/launch.sh" & " > /dev/null 2>&1"'
cp ui/DaVinciBot.icns ~/Applications/DaVinci\ Bot.app/Contents/Resources/applet.icns && codesign --force --deep -s - ~/Applications/DaVinci\ Bot.app
brew install libraqm   # วางสระ/วรรณยุกต์ไทยถูกตำแหน่งในซับที่เรนเดอร์
```

ต้องมี `ffmpeg` และ `whisper-cli` ส่วนโมเดล Whisper ตั้งค่าผ่าน env `WHISPER_MODEL` ได้ (ค่าเริ่มต้นคือ `~/.cache/hyperframes/whisper/models/ggml-large-v3.bin`)

## ข้อจำกัด

- ต้องกดเปิด bridge เองทุกครั้งที่เปิด Resolve
- ซับคลิปสั้นถูกเรนเดอร์เป็นคลิปภาพ (แก้ข้อความได้ด้วยการสั่ง Claude ให้เรนเดอร์ใหม่) ส่วนซับคลิปยาวบน subtitle track แก้ข้อความใน Resolve ได้ แต่ฟอนต์ของ subtitle track ต้องตั้งเองใน Inspector
- ไฟล์ซับ `.srt`, `captions.json` และคลิปซับอยู่ที่ `~/.cache/davinci-bot/captions/<timeline>/`
- fps ของโปรเจกต์ตั้งได้เฉพาะก่อนมี timeline แรก
- B-roll: ค้นจาก Openverse, Internet Archive, NASA, Imgflip (มีม) โดยไม่ต้องใช้ key ใส่ `PEXELS_API_KEY` / `PIXABAY_API_KEY` (ฟรี) เพื่อได้ stock video คุณภาพสูง และใส่ `WIKIMEDIA_CONTACT` (URL หรืออีเมลของคุณ) เพื่อเปิดใช้ Wikimedia ตรง
- คลังเพลงและ SFX ตั้งค่าที่ `library.json` (ค่าเริ่มต้น: `~/Desktop/Agent-M/SFX`, `~/Desktop/Agent-M/MUSIC`) เพลงที่ duck แล้วถูกเรนเดอร์เป็นไฟล์ WAV ต่อช่วง ถ้าแก้ timeline ต้องสั่งวางเพลงใหม่
