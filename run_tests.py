"""
run_tests.py — one command, every check.

    python run_tests.py            # all offline checks (default)
    python run_tests.py --live     # include the tests that call a real provider
    python run_tests.py --quiet    # summary only

What it does
    1. compiles every .py in the project (a syntax error anywhere is a
       failure, not a surprise at launch time);
    2. runs every _refwork/sonde_*.py and classifies the result:
         PASS  — the sonde printed "ERRORS: none"
         PASS  — a diagnostic probe that exited 0 without that line
         SKIP  — needs PyQt6 / audio, which this environment does not have
         FAIL  — anything else, with the first failing line shown
    3. prints one summary and returns exit code 0 or 1, so a CI or a human
       can trust a single number.

Why a script and not pytest: the sondes already exist and encode real
behaviour (gate confirmation, SSE parsing, routing verbs). Wrapping them in
pytest would add a dependency without adding a check. This runs them as they
are, on whatever Python is running it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SONDE_DIR = ROOT / "_refwork"
SKIP_MARKERS = ("No module named 'PyQt6'", "No module named 'sounddevice'",
                "No module named 'PyQt6.QtWidgets'",
                # Audit 25 Okt (M-1): ui_face3d butuh PyOpenGL (GPU). Di mesin
                # tanpa OpenGL sonde_face3d tak bisa jalan — sama halnya dengan
                # PyQt6/audio di atas, ini SKIP lingkungan, bukan kegagalan kode.
                "No module named 'OpenGL'")


def _run(cmd, cwd=None, timeout=600):
    # Audit 25 Okt (M-1): di Windows anak proses mewarisi codec cp1252 untuk
    # stdout, sehingga sonde yang mencetak emoji/karakter Indonesia mati dengan
    # UnicodeEncodeError — 11 kegagalan palsu. Paksa mode UTF-8 pada anak dan
    # dekode UTF-8 pada induk.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True,
                           text=True, timeout=timeout, errors="replace",
                           encoding="utf-8", env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:                           # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def compile_all() -> tuple[bool, list[str]]:
    """Every .py outside _refwork/ and caches must at least parse."""
    bad = []
    for path in ROOT.rglob("*.py"):
        parts = set(path.parts)
        if "__pycache__" in parts or "_refwork" in parts:
            continue
        if ".venv313" in parts or "_legacy" in parts:
            continue
        code, out = _run([sys.executable, "-m", "py_compile", str(path)])
        if code != 0:
            bad.append(f"{path.relative_to(ROOT)}: {out.strip().splitlines()[-1] if out.strip() else 'gagal'}")
    return (not bad), bad


def classify(name: str, code: int, out: str) -> tuple[str, str]:
    if any(m in out for m in SKIP_MARKERS):
        return "SKIP", "butuh PyQt6/audio/OpenGL"
    if "ERRORS: none" in out:
        n = out.count("[OK]")
        return "PASS", f"{n} cek" if n else "semua cek"
    if code == 0:
        last = [ln for ln in out.strip().splitlines() if ln.strip()]
        return "PASS", (last[-1][:60] if last else "exit 0")
    fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
    detail = fails[0][:80] if fails else (out.strip().splitlines()[-1][:80]
                                          if out.strip() else "gagal")
    return "FAIL", detail


def main() -> int:
    quiet = "--quiet" in sys.argv
    live = "--live" in sys.argv
    t0 = time.time()

    if not quiet:
        print("=" * 68)
        print("JARVIS — uji lengkap")
        print("=" * 68)

    ok_compile, bad = compile_all()
    print(("  [OK ] " if ok_compile else "  [FAIL] ")
          + f"compile semua berkas"
          + ("" if ok_compile else f" — {len(bad)} gagal"))
    for line in bad[:10]:
        print("         " + line)

    sondes = sorted(SONDE_DIR.glob("sonde_*.py"))
    if not live:
        # live tests call a real provider and cost seconds; skip by name.
        live_names = {"sonde_provider.py"}
        sondes = [s for s in sondes if s.name not in live_names]

    results = []
    for s in sondes:
        code, out = _run([sys.executable, str(s)], timeout=900)
        status, detail = classify(s.name, code, out)
        # A sonde that talks to a real provider or a network service can fail
        # for reasons that are not the code: a read timeout, an empty answer,
        # a 503. One retry separates those from a real regression — and when
        # the retry passes the result is reported as FLAKY, never as a plain
        # pass, so a test that only works on the second try stays visible.
        if status == "FAIL":
            code2, out2 = _run([sys.executable, str(s)], timeout=900)
            st2, d2 = classify(s.name, code2, out2)
            if st2 == "PASS":
                status, detail = "FLAKY", f"{d2} (lolos percobaan ke-2)"
            else:
                detail = f"{detail} (gagal 2x)"
        results.append((s.name, status, detail))
        if not quiet:
            print(f"  [{status:<5}] {s.name:<28} {detail}")

    # GrokBot 22 Okt: unit test baru di tests/ (offline, pakai mock) ikut
    # dijalankan sebagai satu baris hasil.
    ROOT_DIR = Path(__file__).resolve().parent
    if (ROOT_DIR / "tests").is_dir():
        code, out = _run([sys.executable, "-m", "unittest", "discover",
                          "-s", "tests", "-t", "."], timeout=900)
        tail = (out.strip().splitlines() or [""])[-1]
        st = "PASS" if code == 0 else "FAIL"
        results.append(("tests/ (unittest)", st, tail))
        if not quiet:
            print(f"  [{st:<5}] {'tests/ (unittest)':<28} {tail}")

    passed = sum(1 for _, st, _ in results if st == "PASS")
    failed = sum(1 for _, st, _ in results if st == "FAIL")
    flaky = sum(1 for _, st, _ in results if st == "FLAKY")
    skipped = sum(1 for _, st, _ in results if st == "SKIP")

    print("-" * 68)
    print(f"  compile : {'OK' if ok_compile else 'GAGAL'}")
    print(f"  sonde   : {passed} lolos · {failed} gagal · {flaky} flaky · "
          f"{skipped} dilewati ({len(results)} berkas)")
    print(f"  waktu   : {time.time() - t0:.1f} s")
    print("  HASIL   : " + ("SEMUA BAIK" if ok_compile and failed == 0
                            else "ADA YANG GAGAL"))
    return 0 if (ok_compile and failed == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
