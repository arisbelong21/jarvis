"""sonde_trainer_fixes.py — verifikasi semua perbaikan trainer (harus MERAH dulu).

Menguji B1-B7, G1, P1, P2, P4, G2, G4 langsung pada kode produksi.
Jalankan: .venv313\\Scripts\\python.exe _refwork/sonde_trainer_fixes.py
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import intent, decision_loop as dl                 # noqa: E402
from core.custom_brain import CustomBrain                    # noqa: E402
from actions import code_helper as ch                        # noqa: E402
from actions import file_controller as fc                    # noqa: E402
from memory import memory_manager as mm                      # noqa: E402

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {str(detail)[:60]} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


SB = Path.home() / "jarvis_trainer_fix"
PROBE = Path.home() / "jarvis_trainer_probe"
for d in (SB, PROBE):
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
(PROBE / "teks.txt").write_text("klien A dan klien B.", encoding="utf-8")

# ── B1: negasi ─────────────────────────────────────────────────────────────
check("B1 'jangan kirim email itu' -> refusal",
      intent.classify_turn("jangan kirim email itu") == "refusal",
      intent.classify_turn("jangan kirim email itu"))
check("B1 'jangan hapus file' -> refusal",
      intent.classify_turn("jangan hapus file itu") == "refusal",
      intent.classify_turn("jangan hapus file itu"))
check("B1 REGRESI 'kirim email ke budi' -> work_request",
      intent.classify_turn("kirim email ke budi") == "work_request",
      intent.classify_turn("kirim email ke budi"))
check("B1 REGRESI 'stop' tetap control",
      intent.classify_turn("stop") == "control", intent.classify_turn("stop"))

# ── B2: izin bersyarat ─────────────────────────────────────────────────────
check("B2 'ya tapi jangan kirim' -> bukan izin",
      dl.is_approval("ya tapi jangan kirim") is False)
check("B2 'jangan kirim dulu ya' -> bukan izin",
      dl.is_approval("jangan kirim dulu ya") is False)
check("B2 REGRESI 'ya' -> izin", dl.is_approval("ya") is True)
check("B2 REGRESI 'ya, kirim saja' -> izin",
      dl.is_approval("ya, kirim saja") is True)

# ── B3: access denied ──────────────────────────────────────────────────────
check("B3 'Access denied' -> gagal",
      dl.verify("file_controller", {}, "Access denied: C:\\ProgramData")[0] is False)
check("B3 'Permission denied' -> gagal",
      dl.verify("file_controller", {}, "Permission denied")[0] is False)
check("B3 REGRESI 'Opened ... verified' -> sukses",
      dl.verify("open_app", {}, "Opened Chrome - verified: x")[0] is True)

# ── B4/B5: ucapan pendek ───────────────────────────────────────────────────
check("B4 'ya' -> confirmation",
      intent.classify_turn("ya") == "confirmation", intent.classify_turn("ya"))
check("B5 'tidur' -> control",
      intent.classify_turn("tidur") == "control", intent.classify_turn("tidur"))
check("B4 REGRESI 'ya, buka chrome' bukan confirmation",
      intent.classify_turn("ya, buka chrome") != "confirmation",
      intent.classify_turn("ya, buka chrome"))

# ── B6: perintah berbahaya ─────────────────────────────────────────────────
for c in ("powershell -EncodedCommand AAA", "certutil -urlcache -f http://x y",
          "wmic process call create x", "python -c \"import os;os.system('x')\""):
    check(f"B6 '{c[:30]}' -> destructive",
          dl.classify_command(c) == "destructive", dl.classify_command(c))
check("B6 REGRESI 'dir' -> read", dl.classify_command("dir") == "read")

# ── B7: recall underscore ──────────────────────────────────────────────────
mm.remember("nama_kucing", "Milo", "notes")
check("B7 query 'nama_kucing' menemukan",
      "Milo" in mm.search_memory("nama_kucing", 5),
      mm.search_memory("nama_kucing", 5)[:40])
check("B7 REGRESI 'kucing' menemukan",
      "Milo" in mm.search_memory("kucing", 5))
mm.forget("nama_kucing", "notes")

# ── G1: replace_all ────────────────────────────────────────────────────────
p = SB / "ganti.txt"
p.write_text("klien A dan klien B. Klien C.", encoding="utf-8")
out = ch.code_helper({"action": "replace_all", "file_path": str(p),
                      "old": "klien", "new": "pelanggan"})
check("G1 replace_all mengganti 2 kemunculan",
      p.read_text(encoding="utf-8").count("pelanggan") == 2, out)
check("G1 jumlah dilaporkan", "2" in str(out), out)
check("G1 'Klien' besar tidak ikut (case-sensitive)",
      "Klien C" in p.read_text(encoding="utf-8"))

# ── P1: auto-locate nama tanpa folder ──────────────────────────────────────
found = ch.code_helper({"action": "read", "file_path": "teks.txt"})
check("P1 'teks.txt' ditemukan otomatis di home",
      "klien" in str(found).lower(), found[:60])
missing = ch.code_helper({"action": "read", "file_path": "tidak_ada_999.txt"})
check("P1 tidak ditemukan -> pesan minta tanya folder",
      "folder" in str(missing).lower() or "tanya" in str(missing).lower(),
      missing[:70])

# ── P2: tulis ulang berkas lama ────────────────────────────────────────────
existing = SB / "lama.txt"
existing.write_text("isi lama", encoding="utf-8")
r1 = fc.write_file(str(SB), "lama.txt", "isi baru")
check("P2 write ke berkas lama -> ditolak",
      "refus" in str(r1).lower() or "replace" in str(r1).lower(), r1[:60])
check("P2 isi lama tidak berubah",
      existing.read_text(encoding="utf-8") == "isi lama")
r2 = fc.write_file(str(SB), "lama.txt", "isi baru", overwrite=True)
check("P2 overwrite=True -> boleh",
      existing.read_text(encoding="utf-8") == "isi baru", r2[:50])
r3 = fc.create_file(str(SB), "lama.txt", "isi ketiga")
check("P2 create_file ke berkas lama -> ditolak",
      "refus" in str(r3).lower() or "replace" in str(r3).lower(), r3[:60])
r4 = fc.write_file(str(SB), "baru.txt", "x")
check("P2 REGRESI berkas baru -> boleh", (SB / "baru.txt").exists(), r4[:50])


# ── P4/G2/G4: CustomBrain (LLM palsu) ──────────────────────────────────────
class FakeCall:
    def __init__(self, name, args):
        self.id = "c1"
        self.name = name
        self.args = args


class FakeLLM:
    def __init__(self, script):
        self.script = script
        self.i = 0

    def complete(self, msgs, tools, timeout):
        item = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return item


async def brain_case(script, executor, utter="uji"):
    brain = CustomBrain(FakeLLM(script), "sistem", max_steps=8)
    reply = await brain.turn(utter, [], executor)
    return brain, reply


async def p4_test():
    calls = {"n": 0}

    async def ex(name, args):
        calls["n"] += 1
        return "ok"

    brain, reply = await brain_case([("", [FakeCall("file_controller",
                                                    {"action": "read", "path": "x"})])], ex)
    return brain.max_steps, calls["n"], reply


steps, real_calls, reply = asyncio.run(p4_test())
check("P4 max_steps = 8", steps == 8, steps)
check("P4 berhenti dengan pesan 8 langkah",
      "8 langkah" in str(reply) and "BELUM" in str(reply), reply[:60])
check("G2 panggilan identik -> 1 eksekusi", real_calls == 1, real_calls)


async def g4_test():
    async def ex(name, args):
        return "ok"

    brain, reply = await brain_case(
        [("Sudah saya ganti di teks.txt.", [])], ex)
    return reply


reply4 = asyncio.run(g4_test())
check("G4 klaim tanpa tool -> kalimat jujur",
      "belum" in str(reply4).lower(), reply4[:60])


# ── G5: sapaan bukan tugas ────────────────────────────────────────────────
async def greet_test(utter, reply):
    async def ex(name, args):
        return "ok"

    _b, out = await brain_case([(reply, [])], ex, utter=utter)
    return out


g5a = asyncio.run(greet_test("halo", "Siap saya kerjakan."))
check("G5 sapaan + 'siap saya kerjakan' -> diganti sapaan",
      "siap" not in str(g5a).lower() and "halo" in str(g5a).lower(), g5a[:50])
g5b = asyncio.run(greet_test("kirim email ke budi", "Siap saya kerjakan."))
check("G5 REGRESI tugas nyata -> tidak diubah",
      "siap saya kerjakan" in str(g5b).lower(), g5b[:50])
g5c = asyncio.run(greet_test("halo", "Halo, ada yang bisa dibantu?"))
check("G5 REGRESI sapaan normal -> tidak diubah",
      str(g5c).startswith("Halo, ada"), g5c[:50])

# ── G6: instance kedua ditolak + guard koneksi ────────────────────────────
import importlib.util                                    # noqa: E402
_spec = importlib.util.spec_from_file_location("jmain", str(ROOT / "main.py"))
jmain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jmain)

jmain._single_instance_guard(port=8029)
try:
    jmain._single_instance_guard(port=8029)
    _blocked = False
except SystemExit:
    _blocked = True
check("G6 instance kedua ditolak", _blocked)
check("G6 ConnectionClosed dikenali",
      jmain._is_conn_closed(type("ConnectionClosed", (Exception,), {})())
      and not jmain._is_conn_closed(ValueError("x")))

# ── ringkasan ──────────────────────────────────────────────────────────────
shutil.rmtree(SB, ignore_errors=True)
shutil.rmtree(PROBE, ignore_errors=True)
print("ERRORS:", errs if errs else "none")
