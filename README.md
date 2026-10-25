<div align="center">

# ⚙️ JARVIS

**The Ultimate Cross-Platform Personal AI Assistant**

A real-time voice AI that can hear, see, speak and control your computer — on
any OS. Built on the Gemini Live API for native audio streaming, with a custom
decision engine, contract-based verification and zero subscriptions.

*Engineered by [@aris](https://www.youtube.com/@aris) as a real-world JARVIS-style assistant.*

[![Python](https://img.shields.io/badge/Python-3.11_%7C_3.12_%7C_3.13-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/OS-Windows_%7C_macOS_%7C_Linux-lightgrey)](#-requirements)
[![UI](https://img.shields.io/badge/UI-PyQt6-41cd52?logo=qt&logoColor=white)](#-architecture-highlights)
[![License](https://img.shields.io/badge/License-CC_BY--NC_4.0-orange)](#-license)
[![Status](https://img.shields.io/badge/status-active-success)](#-roadmap)

[**Features**](#-features) · [**Architecture**](#-architecture-highlights) · [**Quick Start**](#-quick-start) · [**Your Data**](#-your-data--security)

</div>

---

## ✨ Overview

JARVIS is not a chat window bolted onto a text model. It is a **live assistant**:
it listens with your microphone, watches your screen and webcam, answers in a
natural voice, and drives the machine — launching apps, moving files, controlling
the browser, adjusting settings, playing video on the HUD itself.

Under the voice sits a **central decision loop** that every request flows through,
whatever the entry point — voice, typed text, the autonomous agent, a plugin,
Telegram or the phone dashboard. That loop decides *intent → permission → execute →
verify → reply*, and it verifies results against **structured evidence (a contract)**
rather than guessing from wording. If an action did not actually work, JARVIS says
so instead of claiming success.

The face is rendered entirely in software — a holographic head with real lip-sync,
no GPU and no extra packages required.

---

## 🚀 Features

### The assistant
| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Ultra-low-latency conversation in any language via the Gemini Live API |
| 🧠 Custom Brain | A hybrid engine — Gemini for the voice, a local decision provider for reasoning |
| 🎧 Audio Device Picker | Choose mic & speakers by name, from a filtered **and measured** device list |
| 🎚️ Push-to-Talk | Hold **Ctrl+Space** and the mic opens — closed the rest of the time |
| 🎙️ Wake Word | Local **"Hey Jarvis"** detection; sleeps until called, never streams while asleep |
| 🔇 Self-Echo Guard | Never answers its own voice — recognises and drops the tail of its own speech |
| ⚡ Instant Acknowledgment | Speaks a short, context-aware reply in **your language** the instant a long task starts |
| 🔗 Session Continuity | A dropped connection, voice change or device change no longer wipes the conversation |
| ♾️ Unlimited Sessions | Sliding-window context compression — one conversation can last for hours |
| ⌨️ Hybrid Input | Switch seamlessly between typing and voice |

### The face & HUD
| Feature | Description |
|---|---|
| 🧑‍🎤 Holographic Avatar | An animated head drawn in software — real facial geometry, lit and rendered, no GPU |
| 👄 Real Lip-Sync | ~50 mouth shapes/second from the audio's formants **and** the transcript |
| 🌍 Language-Free Mouth | Articulation derived by Unicode reduction — Latin, Cyrillic and Greek from one rule set |
| 🙂 Facial Acting | Brows track the phrase, gaze saccades, natural blinking, a small nod on stress |
| 😐 Face as Status | Looks away while thinking, meets your eyes while listening, lids fall while asleep |
| 📺 Video on the HUD | Plays YouTube, a local file or any URL **where the avatar sits** — starts muted |
| 🎨 Live Theming | Recolour the whole HUD from a hue wheel or hex; the avatar retints with it |
| 〰️ Reactive HUD | Waveform pulses to real audio — your mic listening, JARVIS speaking |
| 🗺️ Dynamic Content Panel | Scrollable layer beneath the HUD for web results, news and search data |

### Doing things
| Feature | Description |
|---|---|
| 🖥️ System Control | Launch apps, adjust volume/brightness, WiFi, shortcuts, power — by voice |
| 🌐 Browser Control | Open URLs, navigate tabs, interact with the browser |
| 📂 File Operations | Move, rename, create, write, organise — with a real undo |
| 🖱️ Desktop Control | Taskbar, window management, desktop-level operations |
| 🧩 Autonomous Agent | High-level planning for complex multi-step goals |
| 👁️ Visual Awareness | Screen capture & webcam vision piped into the session, labelled by source |
| 🔍 Multi-Mode Web Search | `news` / `research` / `price` / `compare` / `search` — grounded first, DDG fallback |
| 📨 Send Message | Compose and send via WhatsApp, Telegram and more |
| ⏰ Smart Reminders | OS-native scheduled notifications (Task Scheduler / LaunchAgent / systemd) |
| 📊 Hardware Monitoring | Continuous CPU, RAM, GPU and temperature telemetry with voice alerts |
| 🌤️ Weather | Live weather for your city, personalised from memory |
| 📋 Clipboard Intelligence | Copy text → floating panel with Translate / Summarise / Explain / Fix |

### Trust & memory
| Feature | Description |
|---|---|
| 🧠 Recallable Memory | No size limit, nothing silently forgotten — the prompt carries what fits, the rest is searched on demand |
| 👁️ Memory Panel | See every stored fact, when it was learned, and delete any of it in one click |
| ↩️ Undo | Take back files it moved/renamed/created/wrote and settings it changed |
| ⚠️ Real Confirmation | Shutdown, restart and WiFi wait for a button **you** press — the model cannot confirm its own irreversible actions |
| 🧩 Self-Describing Skills | Every bundled skill declares its own `TOOL` dict + handler, auto-discovered at launch |
| 🧩 Plugin System | Drop a single `.py` into `plugins/` — JARVIS learns a new skill next launch |
| 🪪 Runtime Self-Knowledge | Name, OS, abilities **and limits** generated from the live system each session |
| 🌅 Morning Briefing | Greets you, reads the time, recaps yesterday, fetches live news |
| 🔔 Proactive Check-ins | Time- and context-aware; knows your projects and what you've been discussing |
| 📱 Remote Dashboard | Control the assistant from your phone via QR-code pairing |
| ⚡ Auto-Start on Boot | Registers with the OS startup system (registry / LaunchAgent / .desktop) |
| 🪪 Customization | Change assistant name, your name, voice and colour from the UI — instantly |

---

## 🧠 Architecture Highlights

**One decision loop for every path.** Voice, typed text, the agent, plugins,
Telegram, cloud and the dashboard all funnel into `core/decision_loop.py`:
`intent → gate → execute → verify → reply`. There is no second, weaker path for
"some" requests.

**A permission model, not a convention.** `core/agent/permissions.py` ranks
`deny > confirm > auto_allow > category`. Trust is granted with a time-to-live and
can never be made permanent for destructive or externally-visible actions. The
confirmation token for irreversible commands is issued by the **UI**, not the model
— so the assistant cannot forge its own approval.

**Contract-based verification.** Tools attach structured evidence via
`core/contract.py` (a signed-style sentinel carrying `ok` / `tool` / `note`).
Verification reads that evidence instead of pattern-matching prose, and an
`honest_reply` layer corrects over-claims. When something is unverified, JARVIS
says so rather than announcing success.

**A model ladder that stays up.** Every one-shot Gemini call goes through
`core/gemini.py` with a real timeout and an ordered fallback ladder. On quota,
timeout or outage the next rung takes the call and the failing model is put on a
cooldown, so a single unwell alias can't hang a request or be retried forever.

**Memory that separates storage from prompt budget.** Nothing is silently deleted;
the prompt carries a budgeted core plus an index of keys it had no room for, and
the rest is fetched locally on demand (`recall_memory`) — no network, no second
model.

---

## ⚡ Quick Start

```bash
git clone https://github.com/aris/JARVIS.git
cd JARVIS
python setup.py        # installs the deps YOUR OS needs (skips wrong-OS packages)
python main.py
```

`setup.py` only installs what your operating system needs — Windows-only
libraries are skipped on macOS/Linux and vice-versa — and checks your Python
version up front. Prefer manual? `pip install -r requirements.txt` works too.

> **Note:** If you hit a `ModuleNotFoundError` for an OS-specific package, install
> it with `pip install <module_name>`. The optional **wake word** engine is *not*
> installed here — grab it in one click from **⚙ → WAKE WORD** inside the app.

On first launch you'll be asked for a **free Gemini API key**
([aistudio.google.com](https://aistudio.google.com/app/apikey)); it is stored
locally in `config/api_keys.json` and never committed.

### Run the tests
```bash
python run_tests.py    # compiles all sources, runs the suite + probes, exits 0 when green
```

---

## 📋 Requirements

| Requirement | Details |
|---|---|
| **OS** | Windows 10/11, macOS, or Linux |
| **Python** | 3.11, 3.12 or 3.13 |
| **Microphone** | Required for voice interaction (and the "Hey Jarvis" wake word) |
| **Speakers** | Required for voice replies |
| **API Key** | Free Gemini API key (entered on first launch → `config/api_keys.json`) |
| **GPU** | **Not required.** Avatar and HUD video are rendered in software |
| **YouTube on the HUD** | `yt-dlp`, installed by `setup.py`. Without it, local files & direct URLs still play |
| **Wake word** *(optional)* | One-click download from ⚙ → WAKE WORD (`openwakeword`, a few MB, fully local) |

---

## 🗂️ Project Structure

```
JARVIS/
├── main.py                   # Core loop — Live session, audio I/O, visemes, tool dispatch
├── ui.py                     # PyQt6 HUD — avatar canvas, waveform, log, settings, camera, video
├── setup.py                  # OS-aware installer
├── run_tests.py              # Test harness — compiles sources, runs tests/ + probes
├── core/
│   ├── decision_loop.py      # intent → gate → execute → verify → reply (every entry path)
│   ├── contract.py           # Structured verification evidence — no string-matching
│   ├── gemini.py             # One place for one-shot Gemini calls — ladder, timeouts, cooldowns
│   ├── prompt.txt            # Prompt wording; {tokens} filled from the live system at startup
│   ├── agent/permissions.py  # deny > confirm > auto_allow > category; trust with TTL
│   ├── undo.py               # One shared undo stack — actions register how to reverse themselves
│   ├── confirm.py            # Irreversible-action gate — token issued by the UI, not the model
│   ├── avatar.py / avatar_mesh.py / viseme.py   # Face geometry, lighting, mouth shapes
│   ├── echo.py               # Separates your voice from the assistant's own echo
│   ├── audio_devices.py      # Mic/speaker list — filtered, measured, resolved by name
│   ├── plugin_loader.py      # Plugin engine — discovery, validation, crash isolation
│   ├── action_loader.py      # Bundled-action engine (the built-in twin of plugin_loader)
│   └── wake_word.py          # Local "Hey Jarvis" detector — own thread, offline, opt-in
├── actions/                  # Bundled skills — each self-describes via TOOL dict + handler.
│                             #   Everything here DRIVES THE COMPUTER (apps, files, browser,
│                             #   desktop, screen, clock, weather, display).
│   ├── web_search.py · screen_processor.py · computer_settings.py · computer_control.py
│   ├── open_app.py · browser_control.py · file_controller.py · file_processor.py
│   ├── send_message.py · weather_report.py · video_player.py · desktop.py
│   ├── reminder.py · system_monitor.py · background_monitor.py · proactive.py
│   └── terminal.py           # Structured run_command with exit code + gated permissions
├── plugins/                  # User skills — drop in one .py; _template.py to copy
├── providers/                # OpenAI-compatible LLM registry (the "custom brain")
├── dashboard/                # FastAPI/uvicorn phone dashboard — TLS, rate-limited, tokened
├── memory/
│   ├── memory_manager.py     # Load/save long-term memory — sessions, monitors, identity
│   └── config_manager.py     # api_keys.json access — key, OS, name, voice, colour, toggles
└── config/
    ├── api_keys.json         # Key, name, voice, colour, toggles — created on first launch (git-ignored)
    └── certs/                # Self-signed TLS pair for the phone dashboard (git-ignored)
```

---

## 🔒 Your Data & Security

Everything stays on your machine. There is no JARVIS server, no telemetry and no
account.

| What | Where | Notes |
|---|---|---|
| Gemini API key, plugin credentials | `config/api_keys.json` | **Plaintext.** Anyone with your user account can read it — treat it like a password file. |
| Dashboard TLS certificate + private key | `config/certs/` | Generated locally, self-signed, never leaves the machine. |
| Linked WhatsApp session | `config/whatsapp_web/` | Hands over your WhatsApp account if leaked. |
| What the assistant remembers about you | `memory/` | Delete the files to make it forget everything. |

All of the above are listed in `.gitignore`, so a fork or pull request cannot leak
them by accident. **If you ever commit `config/api_keys.json` publicly, revoke that
key** at [aistudio.google.com](https://aistudio.google.com/app/apikey) and generate
a new one — removing the file in a later commit does not remove it from history.

Your voice is streamed to Google's Gemini Live API while a session is open; that
is the one thing that leaves your computer, and it stops when you mute or close
the app.

---

## 🗺️ Roadmap

JARVIS is versioned by "Mark". Recent marks:

| Mark | Focus |
|---|---|
| **LII** | Voice picker · live theming · reactive HUD · recallable memory · undo · real confirmation · audio device picker · session continuity |
| **LIII** | Wake word · faster Live engine · instant acknowledgment · self-describing action/plugin architecture |
| **LIV** | Holographic avatar · viseme lip-sync · facial acting · face-as-status · push-to-talk · self-echo guard · runtime self-knowledge & limits |
| **LV** | Video on the HUD · model ladder with measured fallback · split settings drawers · trimmed bundled skill list |
| **LVI+** | Interrupt by voice · conversation history · Telegram remote · full file access · security camera · Obsidian |

---

## 🙏 Third-Party Assets

| Asset | Source | Licence |
|---|---|---|
| `core/face_model.obj` | [MediaPipe](https://github.com/google-ai-edge/mediapipe) canonical face model — 468 vertices of measured human face geometry | Apache License 2.0 |

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.
See [LICENSE](LICENSE) for the full text.

---

<div align="center">

## 👤 Connect with the Creator

Engineered by a developer building a real-world JARVIS-style assistant.

**⭐ Star the repository to support the journey to Mark 100.**

[![YouTube](https://img.shields.io/badge/YouTube-@aris-red?logo=youtube&logoColor=white)](https://www.youtube.com/@aris)
[![Instagram](https://img.shields.io/badge/Instagram-@aris-E4405F?logo=instagram&logoColor=white)](https://www.instagram.com/aris)

</div>
