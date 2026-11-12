# DaVinci Bot: คู่มือติดตั้งบนเครื่องใหม่ (สำหรับ Claude)

> **ถึง Claude ที่กำลังอ่านไฟล์นี้:** ผู้ใช้ต้องการให้คุณติดตั้ง DaVinci Bot บนเครื่องนี้ให้ใช้งานได้เหมือนเครื่องเดิม
> เครื่องใหม่เป็น **Windows** ส่วนเครื่องเดิมเป็น macOS (Apple Silicon)
> ทำตามขั้นตอนทีละข้อ ตรวจผลแต่ละข้อก่อนไปข้อถัดไป และ **ตอบผู้ใช้เป็นภาษาไทย**
> ถ้าขั้นไหนต้องใช้สิทธิ์ admin, ต้องสมัคร API key หรือต้องคลิกใน DaVinci Resolve ให้บอกผู้ใช้ทำเอง

> ⚠️ **สถานะบน Windows: ยังไม่เคยทดสอบจริง** โค้ดถูกแก้ให้รองรับ Windows แล้ว (file lock, path IPC, ฟอนต์, LUT, ตัวติดตั้ง bridge) แต่ทดสอบได้แค่บน Mac
> ถ้าเจอปัญหา ให้ดีบักและแก้บนเครื่องนี้ได้เลย จุดที่น่าจะพังอยู่ในข้อ 11

## 0. Bot นี้คืออะไร

MCP server (`server.py`, Python) ที่ทำให้ Claude สั่งตัดต่อใน **DaVinci Resolve เวอร์ชันฟรี** ได้ พร้อม skill 7 ตัว (rough cut, ซับไทย, B-roll, เพลง/SFX, look แต่งหน้า, วิเคราะห์ creator, เขียนสคริปต์ช่อง Agent-M) และหน้าเว็บ UI (`ui/`)

Resolve ฟรีปิด scripting จากภายนอก bridge จึงสื่อสารผ่านไฟล์:

```
Claude ─ MCP (server.py) ─ เขียน req_N.lua ──▶ Lua bridge ใน Resolve (dofile)
                        ◀── อ่าน reply.prefs ── fusion:SavePrefs(path)
```

โฟลเดอร์ IPC:
- Windows: `%APPDATA%\davinci-bot\ipc\`
- macOS: `~/Library/Application Support/davinci-bot/ipc/`

**อย่าพยายามเปลี่ยนไปใช้ Python API, socket หรือ RunScript** เพราะเครื่องเดิมทดสอบแล้วว่า Resolve ฟรีบล็อกทั้งหมด (ดูข้อ 9) ส่วนรายละเอียดเครื่องมือทั้งหมดอยู่ใน [README.md](README.md)

## 1. สิ่งที่ต้องมีก่อน (Windows)

ใช้ PowerShell ส่วน Claude Code บน Windows รันคำสั่งผ่าน Git Bash ซึ่งก็ใช้ได้เหมือนกัน

| อย่าง | ตรวจด้วย | ติดตั้ง |
|---|---|---|
| DaVinci Resolve (ฟรี) | เปิดแอปได้ | ผู้ใช้ติดตั้งเองจาก blackmagicdesign.com |
| Git | `git --version` | `winget install Git.Git` |
| uv | `uv --version` | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| ffmpeg + ffprobe | `ffmpeg -version` | `winget install Gyan.FFmpeg` (เปิด terminal ใหม่หลังติดตั้ง เพื่อให้ PATH อัปเดต) |
| whisper.cpp (`whisper-cli.exe`) | `whisper-cli --help` | ดาวน์โหลด zip จากหน้า Releases ของ github.com/ggml-org/whisper.cpp (`whisper-bin-x64.zip` สำหรับ CPU หรือ `whisper-cublas-*-bin-x64.zip` ถ้ามีการ์ดจอ NVIDIA ซึ่งเร็วกว่ามาก) แตกไฟล์ไว้ เช่น `C:\Tools\whisper\` แล้วเพิ่มเข้า PATH หรือตั้ง env `WHISPER_BIN` ให้ชี้ไปที่ `whisper-cli.exe` |
| Claude Code CLI | `claude --version` | ต้องล็อกอินแล้ว |

ถ้าจะดาวน์โหลดหรือติดตั้งอะไร บอกผู้ใช้ก่อนว่าจะติดตั้งอะไรจากที่ไหน

## 2. เอาโค้ดมาที่เครื่องนี้

```bash
cd ~/Desktop   # หรือโฟลเดอร์ที่ผู้ใช้ต้องการ
git clone https://github.com/bigy2012/davinci-bot.git
cd davinci-bot
```

ทุกคำสั่งข้างล่างให้รันจาก root ของ repo

## 3. ติดตั้ง Python deps

```bash
uv sync
```

## 4. โมเดล Whisper

โค้ดหาโมเดลที่ path นี้ (ดู `davinci_bot/media.py`) ซึ่งบน Windows คือ `C:\Users\<ชื่อ>\.cache\hyperframes\whisper\models\`:

- `ggml-large-v3.bin` (ถอดเสียง)
- `ggml-silero-v5.1.2.bin` (VAD ตัดช่วงเงียบ)

```bash
mkdir -p ~/.cache/hyperframes/whisper/models
curl -L -o ~/.cache/hyperframes/whisper/models/ggml-large-v3.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
curl -L -o ~/.cache/hyperframes/whisper/models/ggml-silero-v5.1.2.bin https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin
```

ไฟล์ large-v3 ใหญ่ประมาณ 3 GB ให้แจ้งผู้ใช้ก่อนดาวน์โหลด ถ้าอยากเก็บไว้ที่อื่น ตั้ง env `WHISPER_MODEL` / `WHISPER_VAD_MODEL` ตอนลงทะเบียน MCP (ข้อ 6)
ถ้าใช้ CPU อย่างเดียว large-v3 จะช้ามาก ให้บอกผู้ใช้ตามจริง

## 5. ติดตั้ง bridge เข้า Resolve

```bash
uv run python install_bridge.py
```

(ไฟล์ `install_bridge.sh` ใช้ได้เฉพาะ Mac ส่วน `install_bridge.py` ใช้ได้ทั้งสองระบบ)

สคริปต์จะเขียน `DaVinci Bot Bridge.lua` ไปที่ `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\` โดยใส่ path ของ repo และ IPC ลงไป
**ถ้าย้ายโฟลเดอร์ repo ภายหลัง ต้องรันคำสั่งนี้ใหม่**

ถ้า Resolve ไม่แสดงเมนู Workspace → Scripts → DaVinci Bot Bridge ให้หาโฟลเดอร์ Scripts ที่ Resolve เวอร์ชันนี้ใช้จริง (อาจเป็น `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\`) แล้วแก้ `dest` ใน `install_bridge.py`

## 6. ลงทะเบียน MCP server

```bash
claude mcp add --scope user davinci-bot -- uv run --directory "$(pwd -W 2>/dev/null || pwd)" server.py
claude mcp get davinci-bot   # ต้องขึ้น ✔ Connected
```

(`pwd -W` ใน Git Bash จะได้ path แบบ `C:/...` ถ้า `uv` หาไม่เจอ ให้ใส่ path เต็มของ `uv.exe` ซึ่งปกติอยู่ที่ `C:\Users\<ชื่อ>\.local\bin\uv.exe`)

env เสริม (ไม่ใส่ก็ใช้งานได้) ใส่ด้วย `-e KEY=value` ก่อน `--`:

| env | ใช้ทำอะไร |
|---|---|
| `WHISPER_BIN` | path เต็มของ `whisper-cli.exe` ถ้าไม่ได้อยู่ใน PATH |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | stock video คุณภาพสูงสำหรับ B-roll (key ฟรี ผู้ใช้ต้องสมัครแล้วพิมพ์ใส่เอง) |
| `WIKIMEDIA_CONTACT` | URL หรืออีเมลของผู้ใช้ เพื่อเปิดใช้ Wikimedia |
| `DAVINCI_BOT_BROLL_DIR` | ที่เก็บ B-roll (ค่าเริ่มต้น `~/Movies/DaVinci Bot B-roll`) |
| `DAVINCI_BOT_CACHE` | cache ซับ/ถอดเสียง (ค่าเริ่มต้น `~/.cache/davinci-bot`) |

อย่าใส่ API key ให้เองถ้าผู้ใช้ไม่ได้ให้มา

## 7. ติดตั้ง skill

skill ของ Claude Code อยู่ที่ `C:\Users\<ชื่อ>\.claude\skills\` บน Windows ให้ใช้ junction (ไม่ต้องเป็น admin) แทน symlink ใน PowerShell ที่ root ของ repo:

```powershell
$skills = "$env:USERPROFILE\.claude\skills"; New-Item -ItemType Directory -Force $skills | Out-Null
foreach ($s in "davinci-rough-cut","davinci-subtitles","davinci-broll","davinci-audio","davinci-look","davinci-creator-analysis","davinci-script") {
  New-Item -ItemType Junction -Path "$skills\$s" -Target "$PWD\skills\$s" -Force | Out-Null
}
```

junction ทำให้แก้ `skills/*/SKILL.md` ใน repo แล้ว Claude เห็นทันที ถ้าสร้าง junction ไม่ได้ ให้ copy โฟลเดอร์แทน (แต่ต้อง copy ใหม่ทุกครั้งที่ `git pull`)

### skill เขียนสคริปต์ `action-story-channel-script`

`davinci-script` (ฟังก์ชัน 7) อ่านกฎจาก skill ชื่อ `action-story-channel-script` ซึ่งเครื่องเดิม sync มาจาก claude.ai ของ account เดิม **account ใหม่จะไม่มี skill นี้** ให้เลือกทางใดทางหนึ่ง:

- ผู้ใช้อัปโหลด skill นั้นเข้า claude.ai ของ account นี้ แล้วมันจะ sync มาที่ `~/.claude/skills/synced/...`
- หรือผู้ใช้ copy โฟลเดอร์ `action-story-channel-script` (มี `SKILL.md`) มาจากเครื่องเดิมเอง แล้ววางไว้ที่ `~/.claude/skills/action-story-channel-script/`
  skill นี้ไม่ได้อยู่ใน repo เพราะเป็นข้อมูลส่วนตัวของช่อง

## 8. ฟอนต์, Thai shaping, LUT, โฟลเดอร์งาน

**ฟอนต์ซับ** ต้องมี: `Kanit` (Bold, ExtraBold, Black), `Noto Sans Thai` (Regular, SemiBold, Bold), `Prompt` (Bold) ทั้งหมดฟรีจาก Google Fonts โค้ดหาใน `%LOCALAPPDATA%\Microsoft\Windows\Fonts\` (ติดตั้งแบบ "Install" ปกติ) และ `C:\Windows\Fonts\` (ติดตั้งแบบ "Install for all users") ชื่อไฟล์ต้องตรง เช่น `Kanit-Bold.ttf` ถ้าขาด `render_captions` จะ error `font not found`

**Thai shaping (libraqm)** สำคัญมาก ถ้าไม่มี สระและวรรณยุกต์ไทยในซับจะวางผิดตำแหน่ง ตรวจด้วย:

```bash
uv run python -c "from PIL import features; print(features.check('raqm'))"
```

ถ้าได้ `False`: wheel ของ Pillow บน Windows มี raqm มาในตัว แต่ต้องหา FriBiDi DLL เจอตอนรัน วิธีหนึ่งคือติดตั้ง MSYS2 แล้ว `pacman -S mingw-w64-x86_64-fribidi` จากนั้น copy `libfribidi-0.dll` ไปไว้ใน PATH (หรือข้าง `python.exe` ใน `.venv\Scripts\`) ตามชื่อที่ Pillow เวอร์ชันนั้นต้องการ ดูหัวข้อ raqm ในเอกสารติดตั้งของ Pillow และทดสอบซ้ำจนได้ `True` ส่วนนี้ยังไม่เคยทำบนเครื่องจริง

**LUT:** `apply_look` จะ copy `.cube` ไปที่ `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT\davinci-bot\` เองตอนใช้ครั้งแรก (อาจต้องให้สิทธิ์เขียนโฟลเดอร์นั้น)

**โฟลเดอร์งาน Agent-M** **ไม่ได้อยู่ใน git** ผู้ใช้ต้อง copy มาจากเครื่องเดิมเอง โค้ดและ skill คาดว่าอยู่ที่ `~/Desktop/Agent-M` (= `C:\Users\<ชื่อ>\Desktop\Agent-M`)
- ถ้า Desktop ของผู้ใช้อยู่ใน OneDrive (`C:\Users\<ชื่อ>\OneDrive\Desktop`) ให้ตกลงกับผู้ใช้ว่าจะวางไว้ที่ไหน แล้วแก้ `library.json` (คลัง SFX/MUSIC) และบอกตัวเองว่า path ใน skill ต้องแปลตาม
- ข้างในมี `SFX/`, `MUSIC/`, `LUTs/`, โลโก้, `CLAUDE.md` แบรนด์ช่อง, โฟลเดอร์เรื่องแต่ละตอน (สคริปต์, แกนเรื่อง)
- ถ้าไม่มีโฟลเดอร์นี้ bot ยังตัดต่อได้ แต่ฟังก์ชันเพลง/SFX และสคริปต์จะหาไฟล์ไม่เจอ

**โฟลเดอร์ `reports/` ใน repo** ก็ **ไม่ได้อยู่ใน git** (ถูก `.gitignore` ไว้เพราะเป็นข้อมูลส่วนตัว) ผู้ใช้ต้อง copy มาจากเครื่องเดิมไปไว้ที่ `davinci-bot/reports/` ข้างในมีแผนช่อง (`channel-strategy-agent-m.md`), รายงานวิเคราะห์ creator, แผนฝึก (`training-plan-*.md`) และค่าเดิม (`metrics.json`) ที่ skill `davinci-script`, `davinci-creator-analysis` และเครื่องมือ `practice_feedback` อ่าน
ทำนองเดียวกัน `presets/examples/` (ภาพตัวอย่าง look) ไม่ได้อยู่ใน git แต่ไม่จำเป็นต่อการทำงาน

**ห้าม commit ไฟล์ใน `reports/`, `presets/examples/` หรือข้อมูลส่วนตัวของผู้ใช้ขึ้น repo เพราะ repo นี้เป็น public** และ commit ด้วยอีเมล noreply ของ GitHub เท่านั้น (`git config user.email 121282845+bigy2012@users.noreply.github.com`)

## 9. สิ่งที่ Claude เครื่องเดิมเรียนรู้มา (บันทึกเป็น memory ของเครื่องนี้ด้วย)

เครื่องเดิมเก็บสิ่งเหล่านี้ไว้ใน auto-memory ซึ่งไม่ได้ย้ายมากับ repo ให้บันทึกเป็น memory ของโปรเจกต์นี้บนเครื่องใหม่:

**วิธีทำงานกับผู้ใช้ (สำคัญที่สุด)**
- ผู้ใช้ตัดสินใจ brief เองทุกงาน: แนวตั้ง 9:16 หรือแนวนอน 16:9, ความยาว, ไฟล์, สคริปต์, สไตล์และโฟลเดอร์ B-roll, ซับ, transition, ชื่อ timeline ห้ามเดาค่าที่ผู้ใช้ไม่ได้บอก ให้ถาม (ใช้ prompt `edit_brief`)
- เสนอแผนตัดเป็นตาราง (ลำดับ, ไฟล์, ช่วงเวลา, ข้อความ, B-roll วินาทีไหน) แล้ว **รออนุมัติก่อน** ค่อย build
- build ลง timeline ชื่อใหม่เสมอ ห้ามเขียนทับงานเดิม ทำทีละงาน

**Resolve ฟรี / bridge**
- Resolve ฟรีบล็อก external scripting, เมนู Scripts ไม่แสดงไฟล์ .py, Lua ในเมนูไม่มี io/require/ffi/socket ช่องทางที่ใช้ได้คือ `dofile(path)` อ่านคำสั่ง และ `fusion:SavePrefs(path)` เขียนคำตอบ (ทดสอบบน Mac, Windows น่าจะเหมือนกัน)
- ผู้ใช้ต้องกด **Workspace → Scripts → DaVinci Bot Bridge** ทุกครั้งที่เปิด Resolve ถ้า tool บอกว่า bridge ไม่ทำงาน ให้ขอให้ผู้ใช้กด แล้วลองใหม่
- ผู้ใช้อาจรันหลาย session ของ Claude บน bridge เดียวกัน bridge ที่ busy อาจดูเหมือนตาย **ห้ามสั่ง relaunch เองหรือสลับโปรเจกต์ Resolve โดยไม่ถาม** (bridge เขียน `beat.prefs` บอกสถานะ idle/busy)
- จะเพิ่มความสามารถใหม่ให้แก้ `resolve_script/ops.lua` (โหลดใหม่ทุกคำสั่ง ไม่ต้องรีสตาร์ท bridge)
- Resolve list table มี key `__flags` ติดมา, `AddTransition` ต้องใช้ `category="simple"`

**รสนิยม rough cut**
- คลิปยาว: ไม่ใช้ punch-in zoom (ผู้ใช้บอกว่าดูแปลก) ส่วน Shorts แนวตั้งให้ถามก่อนทุกงาน
- ช่องว่างระหว่างวลีต้องแน่น: คลิปยาวใช้ head 0.05 / tail 0.10, head 0.13 หลัง L-cut, ท้ายฉาก ~0.5s; Shorts ใช้ head 0.02 / tail 0.02 (ช่องว่าง ≈0.04s), tail ของ segment สุดท้าย 0.4
- ตัดช่วงเงียบกลางประโยคที่ยาวเกิน 0.2s ทิ้งด้วย ไม่งั้นผู้ใช้จะได้ยินเป็น "พูดขาดช่วง"
- ซับ Shorts ห้ามบังหน้า: ฟุตเทจกล้องโต๊ะชุดนี้ใช้ `render_captions` override `{"y": 0.18}` (เหนือหัว)
- Shorts: ใช้ CTA เดียวกับ Short 1 ปิดท้ายทุกคลิป เว้นแต่ผู้ใช้สั่งอื่น
- B-roll 16:9 บน timeline 9:16 ต้องใช้ `fill_zoom=3.16` ถ้าผู้ใช้อยากให้เต็มจอ

**ช่อง Agent-M / สคริปต์**
- ช่องเปลี่ยนแนวเป็น Digital & Audio Forensics ("นักสืบดิจิทัลและผู้ถอดรหัสเสียงปริศนา") แผนเต็มอยู่ที่ `reports/channel-strategy-agent-m.md`
- คำขอสคริปต์มักมาเป็นตาราง 3 คอลัมน์: หมวดหมู่ | หัวข้อคอนเทนต์ (Hook/Title) | การเปรียบเทียบ/การเล่าเรื่อง
- ก่อนเขียนสคริปต์ทุกครั้ง ให้เขียนไฟล์ "แกนเรื่อง" (`<topic>_core_vN.md`) ภาษาง่ายๆ ก่อน (ลำดับเหตุผล เพราะ/เลย/แต่/ดังนั้น, ตัวละครพร้อมคำอ่านไทย, อธิบายเทคนิคเชิงลึก, คำถามทดสอบความเข้าใจ, การ์ดคีย์เวิร์ด) เพื่อให้ host เข้าใจแล้วเล่าด้วยคำของตัวเอง
- รายละเอียดอยู่ใน `skills/davinci-script/SKILL.md`

## 10. ทดสอบว่าใช้งานได้

1. ให้ผู้ใช้เปิด DaVinci Resolve เปิดโปรเจกต์ทดสอบ (ไม่ใช่งานจริง) แล้วกด **Workspace → Scripts → DaVinci Bot Bridge**
2. **เริ่ม Claude Code session ใหม่** (MCP และ skill ที่เพิ่งลงจะโหลดตอนเริ่ม session)
3. `resolve_status` ต้องได้ชื่อโปรเจกต์กลับมา และ `list_timelines` ต้องแสดง timeline ได้
4. `import_media` ไฟล์วิดีโอสั้นๆ 1 ไฟล์ 2 รอบ แล้ว `list_media_pool` ต้องเห็นคลิปเดียว (ไม่ซ้ำ) เพื่อเช็คเรื่อง path ในข้อ 11
5. `transcribe` ไฟล์เดียวกัน เพื่อเช็ค whisper
6. `draft_captions` → `render_captions` สั้นๆ แล้วดูภาพว่าสระ/วรรณยุกต์ไทยวางถูก
7. (เลือกทำ) หน้าเว็บ: `uv run python ui/app.py` แล้วเปิด `http://127.0.0.1:8765` (`ui/launch.sh` และแอป `.app` ใช้ได้เฉพาะ Mac)

## 11. จุดที่น่าจะต้องแก้บน Windows

ไล่ตามลำดับนี้ถ้าข้อ 10 ไม่ผ่าน:

- **`resolve_status` timeout:** เช็คว่ากด bridge แล้ว, `install_bridge.py` รันจาก path repo ปัจจุบัน, มีไฟล์ `beat.prefs` เกิดขึ้นใน `%APPDATA%\davinci-bot\ipc\` ไหม ถ้าไม่เกิด แปลว่า Lua bridge ไม่ได้รันหรือเขียนไฟล์ไม่ได้ ให้เปิด Workspace → Console ใน Resolve ดู error
- **อ่าน/เขียนไฟล์ชนกัน:** Windows ล็อกไฟล์ที่เปิดอยู่ ถ้า Resolve เขียน `reply.prefs` ไม่ได้ตอนที่ Python กำลังอ่าน ให้ดู `_read_reply` ใน `davinci_bot/bridge.py`
- **คลิปถูก import ซ้ำ:** `find_by_path` ใน `resolve_script/ops.lua` เทียบ path แบบตรงตัว ถ้า Resolve คืน path ที่ตัวพิมพ์เล็ก/ใหญ่หรือ `\` / `/` ต่างจากที่ Python ส่งไป ให้ normalize ก่อนเทียบ (lowercase + แปลง `/` เป็น `\`)
- **path ภาษาไทย/เว้นวรรค:** ตรวจว่า `dofile` และ `ImportMedia` รับ path ภาษาไทยได้ ถ้าไม่ได้ ให้แนะนำผู้ใช้เก็บไฟล์ในโฟลเดอร์ชื่ออังกฤษ
- **`ffmpeg`/`ffprobe`/`whisper-cli` not found:** MCP server สืบทอด PATH จากตอนที่ Claude Code เริ่ม ให้รีสตาร์ท Claude Code หลังแก้ PATH หรือใส่ path เต็มผ่าน env
- **skill ใช้คำสั่ง `ls -t ~/.claude/...`:** Claude Code บน Windows รันผ่าน Git Bash จึงใช้ได้ ถ้าไม่ได้ ให้ใช้ Glob แทน

เมื่อแก้อะไรได้ ให้ commit แยกไว้ แล้วบอกผู้ใช้ว่าแก้อะไรไป เพื่อเอากลับไปรวมกับเครื่อง Mac ได้

## เช็คลิสต์สรุป

- [ ] Git, uv, ffmpeg, whisper-cli.exe
- [ ] clone repo + `uv sync`
- [ ] โมเดล whisper 2 ไฟล์
- [ ] `uv run python install_bridge.py`
- [ ] `claude mcp add ... davinci-bot` → Connected
- [ ] junction skill 7 ตัว + `action-story-channel-script`
- [ ] ฟอนต์ Kanit / Noto Sans Thai / Prompt
- [ ] `features.check('raqm')` → True
- [ ] copy โฟลเดอร์ Agent-M, `reports/` และ skill `action-story-channel-script` จากเครื่องเดิม + แก้ `library.json` ถ้า path ต่าง
- [ ] `git config user.email` เป็นอีเมล noreply
- [ ] บันทึก memory จากข้อ 9
- [ ] session ใหม่ → ผ่านทุกข้อใน 10

---

### ภาคผนวก: ติดตั้งบน Mac

```bash
brew install ffmpeg whisper-cpp libraqm
git clone https://github.com/bigy2012/davinci-bot.git && cd davinci-bot
uv sync
./install_bridge.sh
claude mcp add --scope user davinci-bot -- "$HOME/.local/bin/uv" run --directory "$PWD" server.py
for s in davinci-rough-cut davinci-subtitles davinci-broll davinci-audio davinci-look davinci-creator-analysis davinci-script; do ln -sfn "$PWD/skills/$s" ~/.claude/skills/$s; done
```

โมเดล whisper, ฟอนต์, Agent-M และ memory ทำเหมือนข้อ 4, 8, 9 แอป `.app` ดูได้ใน README
