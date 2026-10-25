"""core/tool_decls.py — the core tool declarations, out of main.py.

Pure data: fourteen dicts, no logic, no imports. They live here because
main.py is the orchestrator and does not need 275 lines of JSON-shaped text in
the middle of it. Every other tool (actions, plugins, MCP) is discovered at
runtime; these are the ones whose handling is woven into the live session —
vision capture, memory writes, the monitor engine, shutdown.

Moved verbatim: the list is byte-identical to the block that used to sit in
main.py, which is what the sonde checks.
"""

TOOL_DECLARATIONS = [
    # ── Inline tools ─────────────────────────────────────────────────────────
    # These stay here (rather than in an actions/*.py TOOL dict) because their
    # handling is woven into live-session state — vision capture/injection,
    # camera stream, memory writes, the monitor engine, and shutdown. All other
    # tools live in their own action file and are auto-discovered by
    # core.action_loader (see JarvisLive.__init__).
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "switch_model",
        "description": (
            "Ganti MODEL otak custom (AI yang dipakai berpikir) lewat suara. "
            "Pakai saat pengguna bilang 'ganti model AI jadi GLM', 'switch "
            "model ke Claude', 'pakai deepseek', 'ganti ke nemotron', atau "
            "'pakai provider river'. Isi 'model' dengan nama yang disebut "
            "pengguna apa adanya — potongan nama cukup ('glm', 'claude'). "
            "Isi 'provider' hanya bila pengguna menyebutnya. Kalau nama tidak "
            "dikenal, saya akan sebutkan daftar yang tersedia."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "model": {"type": "STRING",
                          "description": "Nama model, mis. 'GLM', 'claude', "
                                         "'deepseek', 'nemotron'."},
                "provider": {"type": "STRING",
                             "description": "river | arisgateway | anymodel "
                                            "(opsional)."},
            },
            "required": [],
        },
    },
    {
        "name": "switch_engine",
        "description": (
            "Ganti MODE MESIN: 'live' (Gemini berpikir sekaligus berbicara — "
            "satu model) atau 'custom' (hibrida: Gemini jadi telinga & suara, "
            "provider custom jadi otak yang berpikir dan memanggil tool). "
            "Pakai saat pengguna bilang 'pakai mode hibrida', 'otaknya pakai "
            "model custom', 'balik ke mode cepat/live', atau 'ganti mesin'. "
            "Perubahan berlaku pada sambungan berikutnya, bukan di tengah "
            "kalimat."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {"type": "STRING",
                         "description": "'live' atau 'custom'."},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "agent_run",
        "description": (
            "Jalankan TUGAS MULTI-LANGKAH sampai selesai: saya susun rencana, "
            "kerjakan satu langkah demi satu, VERIFIKASI tiap langkah, dan "
            "ulangi kalau gagal. Pakai untuk permintaan yang butuh beberapa "
            "aksi berurutan (mis. 'buat laporan dari data ini lalu simpan ke "
            "folder kerja', 'riset lalu tulis dokumennya'). Berjalan di latar "
            "— saya kabari saat tuntas. Untuk satu aksi sederhana, panggil "
            "tool-nya langsung; jangan pakai ini."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task": {"type": "STRING",
                         "description": "Tugas yang harus selesai, apa adanya "
                                        "dari pengguna."},
                "max_steps": {"type": "INTEGER",
                              "description": "Batas langkah (default 10)."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "reload_plugins",
        "description": (
            "Muat ulang folder plugins tanpa restart. Pakai setelah menulis "
            "plugin baru (tool buatan sendiri) supaya tool itu langsung bisa "
            "dipanggil."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "screen_share",
        "description": (
            "Mulai/hentikan BERBAGI LAYAR: Jarvis melihat layar terus-menerus "
            "dan MEMBIMBING pengguna langkah demi langkah — TIDAK mengklik. "
            "Pakai saat pengguna bilang 'bagikan layar', 'lihat layar saya "
            "terus', 'pantau saya', 'bimbing saya di layar', 'saya salah "
            "klik, tolong arahkan'. action: 'on' untuk mulai, 'off' untuk "
            "berhenti. Untuk sekali lihat saja, pakai screen_process."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["on", "off"],
                           "description": "on = mulai berbagi, off = berhenti"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when the user says (in ANY language): close camera, stop camera, "
            "turn off camera, that's creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "model_status",
        "description": (
            "Report which AI model is ACTUALLY answering right now. Call this "
            "whenever the user asks what model you are, which AI is used, "
            "whether the custom API is working, or who does the heavy work. "
            "Returns the conversation model, the custom provider leading the "
            "heavy-task ladder, and which brain answered the most recent task. "
            "Never answer this question from imagination — call this tool."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
]

