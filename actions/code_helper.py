import subprocess
import sys
import json
import re
import time
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR           = get_base_dir()
API_CONFIG_PATH    = BASE_DIR / "config" / "api_keys.json"
DESKTOP            = Path.home() / "Desktop"
MAX_BUILD_ATTEMPTS = 3
# Model choice lives in core/gemini.py, and so does the timeout and the
# fallback ladder. Writing a model name here is what left this file hanging
# forever whenever that one alias was unwell.
from core import gemini


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_gemini(tier: str = gemini.SMART):
    """Writing and fixing code is the reasoning tier; a 60s deadline because a
    whole file can come back."""
    class _W:
        def generate_content(self, contents):
            resp = gemini.call(contents, tier=tier, timeout_ms=60000)
            if resp is None:
                raise RuntimeError("every Gemini model on the ladder failed")
            return resp

    return _W()


def _clean_code(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext_map = {
        "python": ".py", "py": ".py",
        "javascript": ".js", "js": ".js",
        "typescript": ".ts", "ts": ".ts",
        "html": ".html", "css": ".css",
        "java": ".java", "cpp": ".cpp", "c": ".c",
        "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
        "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
    }
    if output_path:
        p = Path(output_path)
        return p if p.is_absolute() else DESKTOP / p
    ext = ext_map.get((language or "python").lower(), ".py")
    return DESKTOP / f"jarvis_code{ext}"


def _find_file(file_path: str) -> tuple[Path, str]:
    """Resolve a path the way a user means it. Absolute stays absolute; a
    relative name is looked for where code_helper saves files (the Desktop)
    first, then the working directory, then by name under the Desktop —
    because the same file lands in different subfolders depending on how it
    was written, and "File not found" on a file that exists is how an edit
    request burns three calls guessing paths. Returns (path, error)."""
    raw = (file_path or "").strip().strip('"').strip("'")
    if not raw:
        return Path(""), "No file path provided."
    p = Path(raw)
    if p.is_absolute():
        return (p, "") if p.exists() else (p, f"File not found: {raw}")
    for candidate in (DESKTOP / p, Path.cwd() / p):
        if candidate.exists():
            return candidate, ""
    # A bare filename: hunt for it under the Desktop before giving up.
    if p.name and "/" not in raw and "\\" not in raw:
        try:
            for hit in DESKTOP.rglob(p.name):
                if hit.is_file() and len(hit.relative_to(DESKTOP).parts) <= 4:
                    return hit, ""
        except Exception:
            pass
        # 22 Okt — cari dulu di home (kedalaman 3, folder berat dilewati)
        # sebelum menyerah: "File not found" pada berkas yang ADA adalah
        # penyebab model menebak folder lalu memutar 12 langkah.
        hits = _search_home(p.name)
        if len(hits) == 1:
            return hits[0], ""
        if len(hits) > 1:
            return hits[0], ("File ditemukan di beberapa tempat: "
                             + ", ".join(str(h) for h in hits[:3])
                             + " — tanyakan pengguna yang mana.")
    return DESKTOP / p, (f"File not found: {raw}. Tanyakan ke pengguna di "
                         f"folder mana berkas itu — jangan menebak Desktop "
                         f"atau Downloads.")


_HEAVY_DIRS = {"AppData", "node_modules", ".git", ".venv313", "__pycache__",
               "$Recycle.Bin", "Windows", "Program Files",
               "Program Files (x86)"}


def _search_home(name: str, limit: int = 2) -> list[Path]:
    """Cari nama berkas di bawah home (BFS, kedalaman 3, folder berat dilewati)."""
    hits: list[Path] = []
    home = Path.home()
    queue: list[tuple[Path, int]] = [(home, 0)]
    while queue and len(hits) < limit:
        d, depth = queue.pop(0)
        if depth > 3:
            continue
        try:
            for child in d.iterdir():
                if child.is_dir():
                    if child.name in _HEAVY_DIRS or child.name.startswith("."):
                        continue
                    queue.append((child, depth + 1))
                elif child.name.lower() == name.lower() and child.is_file():
                    hits.append(child)
                    if len(hits) >= limit:
                        break
        except Exception:
            continue
    return hits


def _read_file(file_path: str) -> tuple[str, str]:
    p, err = _find_file(file_path)
    if err:
        return "", err
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:
        return "", f"Could not read file: {e}"


def _save_file(path: Path, content: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Saved to: {path}"
    except Exception as e:
        return f"Could not save: {e}"


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview   = "\n".join(all_lines[:lines])
    suffix    = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


# ── Resource verification — never ship a broken page ────────────────────────
# A page that references a model or library the model invented loads a 404
# and looks broken, and the user has no way to tell whose fault it is: from
# the outside "JARVIS wrote a broken file" is the only visible fact. Prompts
# asking the model nicely for real URLs do not fix this — it reaches for a
# third-party embed whose model id it has invented every time. So every
# external URL in generated code is CHECKED for real, and the dead ones are
# repaired: a dead embed becomes a self-contained <model-viewer> over a
# model that actually exists, and whatever cannot be repaired safely is
# reported instead of shipped silently.
_VERIFIED_MODEL_VIEWER_JS = "https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js"
_VERIFIED_MODEL_ASTRONAUT = "https://modelviewer.dev/shared-assets/models/Astronaut.glb"
_VERIFIED_MODEL_VEHICLE = ("https://raw.githubusercontent.com/KhronosGroup/"
                           "glTF-Sample-Models/master/2.0/CesiumMilkTruck/"
                           "glTF-Binary/CesiumMilkTruck.glb")

_MODEL_VIEWER_BLOCK = (
    '<model-viewer src="{src}" camera-controls auto-rotate '
    'shadow-intensity="1" style="width:100%;height:600px;'
    'background:#1a1a1a;border-radius:10px"></model-viewer>'
)

_VEHICLE_HINT = re.compile(r"motor|kendaraan|vehicle|mobil|truck|bike|sepeda|car",
                           re.I)


def _http_alive(url: str, timeout: float = 8.0) -> bool:
    """True when the URL answers with a non-error status. HEAD first; one
    streamed GET as a fallback for servers without HEAD."""
    try:
        import requests
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            r = requests.get(url, timeout=timeout, stream=True,
                             allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


def _urls_in(code: str) -> list:
    urls, seen = [], set()
    for u in re.findall(r'https?://[^\s"\'<>)\]]+', code):
        u = u.rstrip(".,;")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _verify_and_repair(code: str, hint: str = "") -> tuple[str, str]:
    """Check every external URL in generated code; repair the dead ones.

    Returns (code, note). The note is a one-line summary for the tool
    result, empty when there was nothing to check. Everything verified
    comes back untouched.
    """
    urls = _urls_in(code)
    if not urls:
        return code, ""
    dead = [u for u in urls if not _http_alive(u)]
    if not dead:
        return code, f"Resource check: {len(urls)}/{len(urls)} links verified."

    fixed, notes = code, []
    vehicle = bool(_VEHICLE_HINT.search(hint or ""))
    sample = _VERIFIED_MODEL_VEHICLE if vehicle else _VERIFIED_MODEL_ASTRONAUT

    # 1) A dead third-party viewer embed (sketchfab & co) — the "3D image"
    #    shortcut — becomes a real, self-contained viewer.
    replaced = False
    if re.search(r"<iframe\b", fixed, re.I):
        block = _MODEL_VIEWER_BLOCK.format(src=sample)
        fixed, n = re.subn(
            r"<iframe\b[^>]*>[\s\S]*?</iframe\s*>|<iframe\b[^>]*/>",
            block, fixed, flags=re.I)
        if n:
            replaced = True
            notes.append(f"{n} embedded viewer(s) were DEAD links (404) — "
                         f"replaced with a working 3D viewer over a "
                         f"verified sample model")

    # 1b) Comments that describe the dead embed survive iframe removal and
    #     then lie to the reader ("Valid ID: 92657e2f…" of a page that no
    #     longer uses it). Drop any comment that names a dead service or
    #     carries a dead link.
    dead_domains = []
    for u in dead:
        m = re.match(r"https?://([^/]+)", u)
        if m and m.group(1) not in dead_domains:
            dead_domains.append(m.group(1))

    def _drop_stale_comment(m):
        low = m.group(1).lower()
        if (any(d in low for d in dead_domains)
                or "sketchfab" in low or "embed api" in low):
            return ""
        return m.group(0)

    fixed, n = re.subn(r"<!--([\s\S]*?)-->", _drop_stale_comment, fixed)
    if n:
        notes.append(f"removed {n} stale comment(s) about the dead embed")

    # 1c) Visible text that still advertises the dead service ("Rendered via
    #     Sketchfab Engine") is equally false after the replacement — swap
    #     the claim for what the page now actually does.
    if replaced:
        for name in ("Sketchfab Engine", "sketchfab engine"):
            if name in fixed:
                fixed = fixed.replace(name, "model-viewer (self-contained)")
                break
        notes.append("visible text updated to name the real viewer")

    # 2) A dead model URL inside a viewer tag is swapped the same way.
    def _swap(m):
        if _http_alive(m.group(2)):
            return m.group(0)
        notes.append(f"model URL was dead — swapped to a verified sample")
        return m.group(1) + sample + m.group(3)
    fixed = re.sub(r'(<model-viewer\b[^>]*\bsrc=")([^"]+)(")', _swap,
                   fixed, flags=re.I)

    # 3) A viewer without its component script renders nothing — inject it.
    if (re.search(r"<model-viewer\b", fixed, re.I)
            and "model-viewer.min.js" not in fixed
            and "model-viewer.js" not in fixed
            and "@google/model-viewer" not in fixed):
        script = f'<script type="module" src="{_VERIFIED_MODEL_VIEWER_JS}"></script>'
        if "</head>" in fixed:
            fixed = fixed.replace("</head>", script + "\n</head>", 1)
            notes.append("injected the model-viewer component script")

    # 4) Whatever is still dead is reported, not shipped silently.
    still = [u for u in _urls_in(fixed) if not _http_alive(u)]
    if still:
        notes.append("dead links left in place (no safe replacement): "
                     + ", ".join(u[:70] for u in still[:3]))

    return fixed, "Resource check: " + "; ".join(notes) + "."


def _has_error(output: str) -> bool:
    error_signals = ["error", "exception", "traceback", "syntaxerror",
                     "nameerror", "typeerror", "stderr", "failed", "crash"]
    return any(s in output.lower() for s in error_signals)


def _take_screenshot() -> Path | None:
    try:
        import pyautogui
        screenshot_path = Path.home() / "Desktop" / f"jarvis_debug_{int(time.time())}.png"
        screenshot = pyautogui.screenshot()
        screenshot.save(str(screenshot_path))
        print(f"[Code] 📸 Screenshot: {screenshot_path}")
        return screenshot_path
    except Exception as e:
        print(f"[Code] ⚠️ Screenshot failed: {e}")
        return None


def _image_to_base64(path: Path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode("utf-8")


_VALID_INTENTS = {"write", "edit", "explain", "run", "build", "screen_debug", "optimize"}


def _detect_intent(description: str, file_path: str, code: str) -> str:
    """
    Language-independent intent detection — NO fixed keyword list.
    Whatever language the user speaks, the description is classified by
    Gemini. If the API is unreachable, it falls back to language-agnostic
    structural hints (does the file exist on disk, was code provided).
    """
    desc        = (description or "").strip()
    file_exists = bool(file_path) and Path(file_path).exists()

    if desc:
        try:
            ctx = []
            if file_path:
                ctx.append(f"a file path is provided (exists on disk: {file_exists})")
            if code:
                ctx.append("an inline code snippet is provided")
            prompt = (
                "Classify a coding assistant request into exactly ONE intent word.\n"
                "The request may be written in ANY language.\n\n"
                f"Request: {desc}\n"
                + (f"Context: {'; '.join(ctx)}\n" if ctx else "")
                + "\nIntents:\n"
                "  write        = create new code from scratch\n"
                "  edit         = modify an existing file\n"
                "  explain      = describe what given code/file does\n"
                "  run          = execute an existing file\n"
                "  build        = write code, run it, and iterate until it works\n"
                "  screen_debug = analyze an error currently visible on the user's screen\n"
                "  optimize     = refactor / clean up / speed up existing code\n\n"
                "Reply with ONLY the intent word, nothing else."
            )
            ans = _get_gemini().generate_content(prompt).text.strip().lower()
            ans = ans.strip("`'\". \n")
            if ans in _VALID_INTENTS:
                return ans
        except Exception as e:
            print(f"[Code] Intent classification failed ({e}) — structural fallback")

    # Structural fallback — not tied to any language
    if file_exists:
        return "edit" if desc else "explain"
    if code:
        return "explain"
    return "write"

def _write(description: str, language: str, output_path: str, player=None) -> tuple[str, Path]:
    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer.
Write clean, working, well-commented {lang} code for the description below.

Rules:
- Output ONLY the code. No explanation, no markdown, no backticks.
- Add helpful inline comments.
- Handle errors and edge cases properly.
- Use modern best practices.
- When the code references external resources (3D models, images, fonts,
  libraries), use URLs that demonstrably exist: public CDNs and well-known
  sample assets (modelviewer.dev/shared-assets, threejs.org examples,
  unpkg/cdnjs for libraries). Never embed a third-party viewer and never
  invent a placeholder domain — a dead link ships a broken page.

Description: {description}

Code:"""

    response = model.generate_content(prompt)
    code     = _clean_code(response.text)
    path     = _resolve_save_path(output_path, lang)
    _save_file(path, code)
    return code, path


def _fix_code(code: str, error_output: str, description: str) -> str:
    model  = _get_gemini()
    prompt = f"""You are an expert debugger.
The code below failed with the following error. Fix it.
Return ONLY the corrected code — no explanation, no markdown, no backticks.

Original goal: {description}

Error:
{error_output[:2000]}

Broken code:
{code}

Fixed code:"""

    response = model.generate_content(prompt)
    return _clean_code(response.text)


def _run_file(path: Path, args: list, timeout: int) -> str:
    interpreters = {
        ".py":  [sys.executable],
        ".js":  ["node"],
        ".ts":  ["ts-node"],
        ".sh":  ["bash"],
        ".ps1": ["powershell", "-File"],
        ".rb":  ["ruby"],
        ".php": ["php"],
    }
    interp = interpreters.get(path.suffix.lower())
    if not interp:
        return f"No interpreter for {path.suffix}."

    try:
        result = subprocess.run(
            interp + [str(path)] + (args or []),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(path.parent)
        )
        output = result.stdout.strip()
        error  = result.stderr.strip()
        parts  = []
        if output: parts.append(f"Output:\n{output}")
        if error:  parts.append(f"Stderr:\n{error}")
        return "\n\n".join(parts) if parts else "Executed with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    except FileNotFoundError:
        return f"Interpreter not found: {interp[0]}."
    except Exception as e:
        return f"Execution error: {e}"


def _build(description, language, output_path, args, timeout, speak=None, player=None) -> str:
    if not description:
        return "Please describe what you want me to build, sir."

    if player:
        player.write_log("[Code] Build started...")

    lang = language or "python"

    try:
        code, path = _write(description, lang, output_path, player)
        print(f"[Code] ✅ Written: {path}")
    except Exception as e:
        msg = f"Could not write initial code: {e}"
        if speak: speak(msg)
        return msg

    last_output = ""
    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        print(f"[Code] 🔄 Attempt {attempt}/{MAX_BUILD_ATTEMPTS}")
        if player:
            player.write_log(f"[Code] Attempt {attempt}...")

        last_output = _run_file(path, args, timeout)

        if not _has_error(last_output):
            msg = (
                f"Build complete, sir. "
                f"The code is working after {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to {path}."
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        print(f"[Code] ⚠️ Error on attempt {attempt}, fixing...")
        if player:
            player.write_log(f"[Code] Fixing (attempt {attempt})...")

        try:
            code = _fix_code(code, last_output, description)
            _save_file(path, code)
        except Exception as e:
            msg = f"Could not fix code on attempt {attempt}: {e}"
            if speak: speak(msg)
            return msg

    msg = (
        f"I was unable to build a working version after {MAX_BUILD_ATTEMPTS} attempts, sir. "
        f"The last error was: {last_output[:200]}"
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast code saved to: {path}"

def _write_action(description, language, output_path, player, code="") -> str:
    if player:
        player.write_log("[Code] Writing code...")
    # A model that hands over finished code gets it saved as-is. The
    # description is only needed when the code still has to be generated —
    # rejecting a complete payload over a missing description is how a
    # single write turns into a wasted retry.
    try:
        if code:
            path = _resolve_save_path(output_path, language)
        else:
            if not description:
                return "Please describe what you want me to write, sir."
            code, path = _write(description, language, output_path, player)
        # Never ship a page whose resources are dead links: every external
        # URL is verified and the dead ones repaired BEFORE the file is
        # called done — a sketchfab embed with an invented model id is a
        # 404 the user can only read as "JARVIS wrote a broken file".
        note = ""
        if (language or "").lower() in ("html", "htm", "web"):
            code, note = _verify_and_repair(code, description or "")
        _save_file(path, code)                     # repaired content wins
        print(f"[Code] ✅ Written: {path}")
        if note:
            print(f"[Code] {note}")
        head = f"Code written. Saved to: {path}"
        if note:
            head += f"\n\n{note}"
        return (
            f"{head}\n\n"
            f"To edit this file later, call code_helper with "
            f"file_path = \"{path}\".\n\nPreview:\n{_preview(code)}"
        )
    except Exception as e:
        return f"Could not generate code: {e}"


def _edit_action(file_path, instruction, player) -> str:
    if not file_path:
        return "Please provide a file path to edit, sir."
    if not instruction:
        return "Please describe what change to make, sir."

    p, err = _find_file(file_path)
    if err:
        return err
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Could not read file: {e}"

    if player:
        player.write_log("[Code] Editing file...")

    model  = _get_gemini()
    prompt = f"""You are an expert code editor.
Apply the following change to the code below.
Return ONLY the complete updated code — no explanation, no markdown, no backticks.

Change: {instruction}

Original code:
{content}

Updated code:"""

    try:
        response = model.generate_content(prompt)
        edited   = _clean_code(response.text)
    except Exception as e:
        return f"Could not edit code: {e}"

    status = _save_file(p, edited)
    print(f"[Code] ✅ Edited: {p}")
    return f"File edited. {status}\n\nPreview:\n{_preview(edited)}"


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain, sir."

    if player:
        player.write_log("[Code] Analyzing code...")

    model  = _get_gemini()
    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Could not explain code: {e}"


def _run_action(file_path, args, timeout, player, code: str = "",
                language: str = "python") -> str:
    """Run a file — or a snippet, which is what the model usually sends.

    The old signature demanded a file path, so a model that had just written
    code and wanted to run it was told "please provide a file path to run" —
    a dead end that reads like the tool refusing to work. A snippet now goes
    to a temporary file and runs the same way.
    """
    if file_path:
        p, err = _find_file(file_path)
        if err:
            return err
    elif str(code or "").strip():
        import tempfile

        suffix = {"python": ".py", "javascript": ".js", "node": ".js",
                  "typescript": ".ts", "bash": ".sh", "shell": ".sh"}.get(
                      (language or "python").lower(), ".py")
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            p = Path(f.name)
    else:
        return ("Please provide a file path to run, or pass `code` with the "
                "snippet itself and I will run it in a temporary file.")
    if player:
        player.write_log(f"[Code] Running {p.name}...")
    return _run_file(p, args, timeout)


def _optimize_action(file_path, code, language, output_path, player) -> str:

    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to optimize, sir."

    if player:
        player.write_log("[Code] Optimizing code...")

    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer and code reviewer.
Optimize the following code for:
1. Performance — eliminate unnecessary operations, use efficient data structures
2. Readability — clear variable names, proper formatting, logical structure
3. Best practices — modern {lang} patterns, error handling, type hints if applicable
4. Remove dead code, redundant comments, and unnecessary complexity

Return ONLY the optimized code — no explanation, no markdown, no backticks.

Original code:
{code[:6000]}

Optimized code:"""

    try:
        response  = model.generate_content(prompt)
        optimized = _clean_code(response.text)
    except Exception as e:
        return f"Could not optimize code: {e}"

    # Kaydet
    if file_path:
        save_path, err = _find_file(file_path)
        if err:
            save_path = _resolve_save_path(output_path, lang)
    else:
        save_path = _resolve_save_path(output_path, lang)

    status = _save_file(save_path, optimized)
    print(f"[Code] ✅ Optimized: {save_path}")

    original_lines  = len(code.splitlines())
    optimized_lines = len(optimized.splitlines())
    diff = original_lines - optimized_lines

    return (
        f"Code optimized. {status}\n"
        f"Lines: {original_lines} → {optimized_lines} "
        f"({'−' if diff > 0 else '+'}{abs(diff)} lines)\n\n"
        f"Preview:\n{_preview(optimized)}"
    )


def _screen_debug_action(description, file_path, player, speak=None) -> str:

    if player:
        player.write_log("[Code] Taking screenshot for analysis...")

    print("[Code] 📸 Capturing screen for debug...")


    screenshot_path = _take_screenshot()
    if not screenshot_path:
        return "Could not take screenshot, sir. Please make sure PyAutoGUI is installed."


    file_content = ""
    if file_path:
        file_content, err = _read_file(file_path)
        if err:
            print(f"[Code] ⚠️ Could not read file: {err}")

    try:
        from google.genai import types

        image_bytes  = screenshot_path.read_bytes()
        image_base64 = _image_to_base64(screenshot_path)

        user_question = description or "What error or problem do you see on the screen? How can it be fixed?"

        context = ""
        if file_content:
            context = f"\n\nAdditionally, here is the related file content:\n```\n{file_content[:4000]}\n```"

        analysis_prompt = f"""You are an expert programmer and debugger analyzing a screenshot.

User's question: {user_question}{context}

Please:
1. Identify any errors, exceptions, or problems visible on the screen
2. Explain what is causing the problem in simple terms
3. Provide a concrete fix or solution
4. If there's code visible, show the corrected version

Be specific and actionable. If you see an error message, quote it exactly."""

        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            analysis_prompt,
        ]

        response = gemini.call(contents, tier=gemini.SMART, timeout_ms=45_000)
        if response is None:
            return "Sir, I couldn't reach Gemini to analyse that screenshot."

        analysis = (response.text or "").strip()
        print(f"[Code] ✅ Screen analysis complete")

        try:
            screenshot_path.unlink()
        except Exception:
            pass

        if file_path and file_content:

            code_match = re.search(r"```[a-zA-Z]*\n(.*?)```", analysis, re.DOTALL)
            if code_match:
                fixed_code = code_match.group(1).strip()
                save_path, err = _find_file(file_path)
                if err:
                    save_path = Path(file_path)
                _save_file(save_path, fixed_code)
                analysis += f"\n\n✅ Fixed code has been saved to: {save_path}"
                print(f"[Code] ✅ Fixed code saved: {save_path}")

        return analysis

    except Exception as e:

        try:
            screenshot_path.unlink()
        except Exception:
            pass
        return f"Screen analysis failed: {e}"



# ── GrokBot 22 Okt: siklus baca → ubah persis → verifikasi ──────────────────
# `edit` meminta model menulis ULANG seluruh berkas (lambat, dan bisa
# mengubah bagian yang tidak diminta). `read`, `search` dan `replace`
# memberi otak kustom alat yang tepat: baca dengan nomor baris, cari teks,
# ganti SATU potongan persis (gagal bila tidak unik), dengan undo dan
# verifikasi baca-ulang sebelum melaporkan selesai.

_PROTECTED = ("api_keys.json", ".pem", ".key", "link.json", "tool_trust.json")
_MAX_READ = 12000


def _protected(path: Path) -> bool:
    low = str(path).lower().replace("\\", "/")
    return any(low.endswith(x) for x in _PROTECTED) or "/.git/" in low


def _read_lines_action(file_path: str, start: int = 1, count: int = 200) -> str:
    p, err = _find_file(file_path)
    if err:
        return err
    if _protected(p):
        return f"Refused: {p.name} holds secrets and is never read by tools."
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"Could not read file: {e}"
    start = max(1, int(start or 1))
    chunk = lines[start - 1:start - 1 + max(1, min(800, int(count or 200)))]
    body = "\n".join(f"{i:>5}| {ln}" for i, ln in enumerate(chunk, start))
    if len(body) > _MAX_READ:
        body = body[:_MAX_READ] + "\n…[dipotong — baca dengan start/count]"
    return f"File: {p} ({len(lines)} lines)\n{body}"


def _search_action(file_path: str, pattern: str) -> str:
    """Cari teks (bukan regex) di satu berkas atau semua berkas teks di folder."""
    if not pattern:
        return "No search text given."
    root = Path(file_path).expanduser() if file_path else DESKTOP
    if not root.is_absolute():
        root, err = _find_file(file_path)
        if err:
            return err
    files = [root] if root.is_file() else [
        f for f in root.rglob("*") if f.is_file() and f.stat().st_size < 2_000_000
        and not _protected(f) and not any(x in f.parts for x in
                                           (".git", "node_modules", "__pycache__", ".venv"))]
    hits = []
    for f in files[:3000]:
        try:
            for n, ln in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if pattern in ln:
                    hits.append(f"{f}:{n}: {ln.strip()[:160]}")
                    if len(hits) >= 60:
                        break
        except Exception:
            continue
        if len(hits) >= 60:
            break
    return ("\n".join(hits) if hits else f"No match for '{pattern}' in {root}.")


def _replace_action(file_path: str, old: str, new: str, player=None) -> str:
    """Ganti SATU potongan persis. Berkas baru dibuat bila old kosong dan
    berkas belum ada. Undo tersedia; hasil diverifikasi dengan baca ulang."""
    raw = (file_path or "").strip().strip('"')
    if not raw:
        return "No file path provided."
    p, err = _find_file(raw)
    if _protected(p):
        return f"Refused: {p.name} holds secrets and is never edited by tools."
    if err:
        if old:
            return err
        # Berkas baru.
        p = Path(raw) if Path(raw).is_absolute() else DESKTOP / raw
        if p.exists():
            return f"Refused: {p} already exists — give 'old' text to edit it."
        res = _save_file(p, new)
        if not p.exists() or p.read_text(encoding="utf-8") != new:
            return f"Could not save: {p} did not read back correctly."
        try:
            from core.undo import push_undo
            push_undo(f"buat {p.name}", lambda: (p.unlink(), f"{p.name} dihapus")[1])
        except Exception as e:
            print(f"[Code] undo tidak tercatat: {e}")
        return f"{res} — created and verified (read back {len(new)} chars)."
    try:
        before = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Could not read file: {e}"
    if not old:
        return "Refused: 'old' is empty — give the exact text to replace."
    n = before.count(old)
    if n == 0:
        return (f"No exact match in {p.name}. Read the file first (action=read) "
                f"and copy the text exactly, including spaces.")
    if n > 1:
        return (f"The text appears {n} times in {p.name}. Include more "
                f"surrounding lines so it is unique.")
    after = before.replace(old, new, 1)
    res = _save_file(p, after)
    try:
        check = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Saved but could not re-read {p}: {e}"
    if check != after:
        return f"Could not verify {p}: the file did not read back as written."
    try:
        from core.undo import push_undo
        push_undo(f"ubah {p.name}", lambda: (p.write_text(before, encoding="utf-8"),
                                             f"{p.name} dikembalikan")[1])
    except Exception as e:
        print(f"[Code] undo tidak tercatat: {e}")
    import difflib
    diff = "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True),
        fromfile=p.name, tofile=p.name, n=1))[:1500]
    line = before[:before.index(old)].count("\n") + 1
    if player:
        try:
            player.write_log(f"[Code] ✏️ {p.name}:{line}")
        except Exception:
            pass
    return f"{res} — replaced 1 block at line {line}, verified by re-reading.\n{diff}"


def _replace_all_action(file_path: str, old: str, new: str, player=None) -> str:
    """Ganti SEMUA kemunculan, laporkan jumlahnya, verifikasi baca-ulang.

    22 Okt — permintaan "ganti semua kata X jadi Y" dulu tidak bisa: replace
    menolak bila teks muncul >1x. Aksi ini menghitung, mengganti semua, lalu
    membaca ulang untuk membuktikan hasilnya. Undo mencatat isi sebelumnya.
    """
    if not (file_path or "").strip():
        return "No file path provided."
    p, err = _find_file(file_path)
    if err:
        return err
    if _protected(p):
        return f"Refused: {p.name} holds secrets and is never edited by tools."
    if not old:
        return "Refused: 'old' is empty — give the exact text to replace."
    try:
        before = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Could not read file: {e}"
    n = before.count(old)
    if n == 0:
        return f"No match in {p.name} — file tidak diubah."
    after = before.replace(old, new)
    res = _save_file(p, after)
    try:
        check = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Saved but could not re-read {p}: {e}"
    if check != after:
        return f"Could not verify {p}: the file did not read back as written."
    try:
        from core.undo import push_undo
        push_undo(f"ubah semua {p.name}",
                  lambda: (p.write_text(before, encoding="utf-8"),
                           f"{p.name} dikembalikan")[1])
    except Exception as e:
        print(f"[Code] undo tidak tercatat: {e}")
    if player:
        try:
            player.write_log(f"[Code] ✏️ {p.name}: {n} penggantian")
        except Exception:
            pass
    return f"{res} — replaced {n} occurrence(s), verified by re-reading."


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    """
    Called from main.py.

    parameters:
        action      : write | edit | explain | run | build | screen_debug | optimize | auto
        description : What the code should do / what change to make / what problem to analyze
        language    : Programming language (default: python)
        output_path : Where to save — user specifies full path or filename
        file_path   : Path to existing file (edit / explain / run / build / optimize)
        code        : Raw code string (explain/optimize without a file)
        args        : CLI argument list for run/build
        timeout     : Execution timeout in seconds (default: 30)
    """
    p           = parameters or {}
    action      = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    language    = p.get("language", "python").strip()
    output_path = p.get("output_path", "").strip()
    file_path   = p.get("file_path", "").strip()
    code        = p.get("code", "").strip()
    args        = p.get("args", [])
    timeout     = int(p.get("timeout", 30))

    if action == "auto":
        action = _detect_intent(description, file_path, code)
        print(f"[Code] 🤖 Auto-detected: {action}")

    if action == "write":
        return _write_action(description, language, output_path, player, code)

    elif action == "edit":
        return _edit_action(
            file_path,
            description or p.get("instruction", ""),
            player
        )

    elif action == "explain":
        return _explain_action(file_path, code, player)

    elif action == "run":
        return _run_action(file_path, args, timeout, player, code, language)

    elif action == "build":
        return _build(description, language, output_path, args, timeout, speak, player)

    elif action == "optimize":
        return _optimize_action(file_path, code, language, output_path, player)

    elif action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)

    elif action in ("read", "read_file", "read_lines"):
        # "read_file" is the name a model reaches for first; refusing it with
        # "unknown action" is how a 143 KB file ended up being copied and split
        # into scratch files. Both names now do the same line-based read.
        return _read_lines_action(file_path, p.get("start", 1), p.get("count", 200))

    elif action == "search":
        return _search_action(file_path, str(p.get("old") or description or ""))

    elif action == "replace":
        return _replace_action(file_path, str(p.get("old") or ""),
                               str(p.get("new") if p.get("new") is not None
                                   else code), player)

    elif action == "replace_all":
        return _replace_all_action(file_path, str(p.get("old") or ""),
                                   str(p.get("new") if p.get("new") is not None
                                       else code), player)

    else:
        return (f"Unknown action: '{action}'. Use write, edit, explain, run, build, "
                f"optimize, screen_debug, read, search, replace, replace_all, or auto.")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "code_helper",
    "description": "Writes, edits, explains, runs, or builds code files. For precise work use read (numbered lines), search (find text), and replace (swap ONE exact block, verified by re-reading, undoable); then check with run_command (tests) before saying it is done.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "write | edit | read | read_file | explain | run | build | optimize | screen_debug | search | replace | replace_all | auto (default: auto)"
            },
            "description": {
                "type": "STRING",
                "description": "What the code should do or what change to make"
            },
            "language": {
                "type": "STRING",
                "description": "Programming language (default: python)"
            },
            "output_path": {
                "type": "STRING",
                "description": "Where to save — full path or filename (relative names land on the Desktop)"
            },
            "file_path": {
                "type": "STRING",
                "description": "Path to existing file for edit/explain/run/build — relative names are resolved against the Desktop automatically"
            },
            "code": {
                "type": "STRING",
                "description": "Raw code string — with action=write it is saved as-is, with explain/optimize it is the code to work on"
            },
            "args": {
                "type": "STRING",
                "description": "CLI arguments for run/build"
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Execution timeout in seconds (default: 30)"
            },
            "old": {
                "type": "STRING",
                "description": "replace: the exact text to replace (must be unique); search: text to find"
            },
            "new": {
                "type": "STRING",
                "description": "replace: the new text (with empty 'old' on a missing file: create it)"
            },
            "start": {
                "type": "INTEGER",
                "description": "read: first line number (default 1)"
            },
            "count": {
                "type": "INTEGER",
                "description": "read: number of lines (default 200)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": code_helper,
}
