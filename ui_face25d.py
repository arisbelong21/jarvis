"""Wajah JARVIS 2.5D — gambar dasar + animasi berlapis (QPainter).

Kenapa bukan OpenGL:
  * tidak berebut dengan thread audio (aplikasi ini pernah tersendat),
  * tidak perlu driver/VAO/shader,
  * cukup untuk yang dibutuhkan: cahaya, denyut, partikel, parallax.

Lapisan, dari belakang ke depan:
  1. glow belakang (radial, denyut ikut level suara)
  2. gambar helm (dasar) + parallax halus
  3. glow mata (dua titik, mengikuti level suara)
  4. sapuan energi (saat berpikir/berbicara)
  5. bara api (partikel naik, muncul saat berbicara)
  6. vignette gelap di tepi supaya menyatu dengan HUD

API untuk ui.py:
    w = Face25DWidget()
    w.set_state("IDLE" | "LISTENING" | "THINKING" | "SPEAKING")
    w.set_audio_level(0.0 .. 1.0)

Uji tanpa membuka aplikasi:
    python ui_face25d.py --shot out.png --state SPEAKING
    python ui_face25d.py --demo
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import QWidget

BASE_DIR = Path(__file__).resolve().parent
# Aset HD (1260x1360) diekstrak dari HTML referensi; versi kecil sebagai
# cadangan kalau berkas HD tidak ada.
BASE_IMG = BASE_DIR / "assets" / "face" / "base_hd.png"
FALLBACK_IMG = BASE_DIR / "assets" / "face" / "base.png"

# Posisi mata pada gambar dasar (fraksi 0..1) — dipakai untuk glow.
EYE_L = (0.372, 0.636)
EYE_R = (0.626, 0.636)

ACCENT = QColor("#ff3b30")        # merah menyala, mengikuti referensi
ACCENT_HOT = QColor("#ffd0b0")    # inti mata


class Face25DWidget(QWidget):
    """Wajah beranimasi dari satu gambar. Ringan, tanpa OpenGL."""

    def __init__(self, parent=None, image: Path | None = None,
                 transparent: bool = False):
        super().__init__(parent)
        # transparent=True: dipakai sebagai LAPISAN di atas HUD lain.
        # Latar tidak diisi dan vignette dilewati, supaya HUD di
        # belakangnya tetap terlihat.
        self._transparent = bool(transparent)
        _p = Path(image) if image else BASE_IMG
        if not _p.is_file():
            _p = FALLBACK_IMG          # aset kecil, kalau yang HD hilang
        self._pix = QPixmap(str(_p))
        self._state = "IDLE"
        self._level = 0.0
        self._speak = 0.0
        self._t = 0.0
        self._sweep = 0.0
        self._embers: list[list[float]] = []      # x, y, vx, vy, life, size
        self._mouse = QPointF(0.0, 0.0)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 240)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)                      # ~30 fps

    # ── API ────────────────────────────────────────────────────────────
    def set_state(self, state: str) -> None:
        self._state = (state or "IDLE").upper()

    def set_audio_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level or 0.0)))

    def state(self) -> str:
        return self._state

    # ── animasi ────────────────────────────────────────────────────────
    def _tick(self) -> None:
        self._t += 0.033
        target = 1.0 if self._state == "SPEAKING" else 0.0
        self._speak += (target - self._speak) * 0.12
        if self._state == "THINKING":
            self._sweep = (self._sweep + 0.012) % 1.0
        if self._state == "LISTENING":
            self._level = max(self._level * 0.95, 0.12)
        self._spawn_embers()
        self._step_embers()
        self.update()

    def _spawn_embers(self) -> None:
        rate = 0.55 * self._speak + 0.12 * self._level
        if random.random() > rate:
            return
        if len(self._embers) > 90:
            return
        self._embers.append([
            random.uniform(0.18, 0.82), 0.92,
            random.uniform(-0.02, 0.02), random.uniform(-0.09, -0.03),
            1.0, random.uniform(1.2, 3.4),
        ])

    def _step_embers(self) -> None:
        alive = []
        for e in self._embers:
            e[0] += e[2] * 0.06
            e[1] += e[3] * 0.06
            e[4] -= 0.012
            if e[4] > 0 and e[1] > -0.05:
                alive.append(e)
        self._embers = alive

    def mouseMoveEvent(self, ev) -> None:
        w = max(1, self.width())
        h = max(1, self.height())
        self._mouse = QPointF((ev.position().x() / w - 0.5) * 2.0,
                              (ev.position().y() / h - 0.5) * 2.0)

    # ── gambar ─────────────────────────────────────────────────────────
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self._transparent:
            p.fillRect(self.rect(), QColor("#05070c"))

        w, h = self.width(), self.height()
        side = min(w, h)
        # kotak gambar: sedikit lebih besar dari sisi pendek
        size = side * (0.80 + 0.008 * math.sin(self._t * 0.9))
        cx = w / 2 + self._mouse.x() * side * 0.012
        cy = h / 2 + self._mouse.y() * side * 0.012
        target = QRectF(cx - size / 2, cy - size / 2, size, size)

        self._draw_back_glow(p, cx, cy, side)
        self._draw_orbits(p, cx, cy, side)
        if not self._pix.isNull():
            # saat diam wajah lebih redup; saat mendengar/berbicara menyala
            # penuh — supaya perpindahan keadaan TERLIHAT, bukan cuma label
            awake = max(self._level, self._speak)
            p.setOpacity(0.80 + 0.20 * awake)
            p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
            p.setOpacity(1.0)
        self._draw_eyes(p, target)
        self._draw_sweep(p, target)
        self._draw_embers(p, target)
        self._draw_waveform(p, cx, cy, side)
        if not self._transparent:
            self._draw_vignette(p)
        p.end()

    def _draw_back_glow(self, p: QPainter, cx: float, cy: float,
                        side: float) -> None:
        pulse = 0.55 + 0.45 * math.sin(self._t * 1.7)
        strength = (0.14 + 0.40 * self._level + 0.70 * self._speak) * pulse
        r = side * (0.46 + 0.04 * self._speak)
        g = QRadialGradient(QPointF(cx, cy + side * 0.02), r)
        c0 = QColor(ACCENT)
        c0.setAlphaF(min(1.0, 0.60 * strength))
        c1 = QColor(ACCENT)
        c1.setAlphaF(0.0)
        g.setColorAt(0.0, c0)
        g.setColorAt(0.55, QColor(255, 40, 24, int(60 * strength)))
        g.setColorAt(1.0, c1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_eyes(self, p: QPainter, box: QRectF) -> None:
        base = 0.26 + 0.70 * self._level + 0.95 * self._speak
        flicker = 0.92 + 0.08 * math.sin(self._t * 9.0)
        for fx, fy in (EYE_L, EYE_R):
            ex = box.left() + box.width() * fx
            ey = box.top() + box.height() * fy
            rad = box.width() * (0.085 + 0.02 * self._speak)
            g = QRadialGradient(QPointF(ex, ey), rad)
            hot = QColor(ACCENT_HOT)
            hot.setAlphaF(min(1.0, 0.85 * base * flicker))
            mid = QColor(ACCENT)
            mid.setAlphaF(min(1.0, 0.45 * base))
            g.setColorAt(0.0, hot)
            g.setColorAt(0.35, mid)
            g.setColorAt(1.0, QColor(255, 30, 20, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(ex, ey), rad, rad * 0.78)
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver)

    def _draw_sweep(self, p: QPainter, box: QRectF) -> None:
        if self._state not in ("THINKING", "SPEAKING"):
            return
        y = box.top() + box.height() * (0.10 + 0.80 * self._sweep)
        g = QLinearGradient(0, y - 14, 0, y + 14)
        c = QColor(255, 90, 60)
        c.setAlphaF(0.22 if self._state == "THINKING" else 0.12)
        g.setColorAt(0.0, QColor(255, 60, 40, 0))
        g.setColorAt(0.5, c)
        g.setColorAt(1.0, QColor(255, 60, 40, 0))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.fillRect(QRectF(box.left(), y - 14, box.width(), 28), QBrush(g))
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)

    def _draw_embers(self, p: QPainter, box: QRectF) -> None:
        if not self._embers:
            return
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _vx, _vy, life, size in self._embers:
            ex = box.left() + box.width() * x
            ey = box.top() + box.height() * y
            c = QColor(255, 120, 70)
            c.setAlphaF(min(1.0, 0.55 * life))
            p.setBrush(QBrush(c))
            p.drawEllipse(QPointF(ex, ey), size, size)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)

    def _draw_orbits(self, p: QPainter, cx: float, cy: float,
                     side: float) -> None:
        """Cincin orbit: beberapa busur berputar + satu titik komet.

        Digambar di belakang wajah; hanya bagian luar yang terlihat, jadi
        terbaca sebagai cincin yang mengelilingi kepala — seperti referensi.
        """
        spin = self._t * (0.30 if self._state != "THINKING" else 0.85)
        # radius < 0.5 supaya tidak terpotong tepi bingkai
        rings = (
            (0.455, 3.2, 0.38, 0.022),   # (radius, jumlah busur, alpha, tebal)
            (0.482, 5.0, 0.24, 0.016),
            (0.505, 2.0, 0.15, 0.012),
        )
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        for idx, (rf, seg, alpha, width) in enumerate(rings):
            r = side * rf
            rect = QRectF(cx - r, cy - r * 0.94, r * 2, r * 1.88)
            rot = spin * (1.0 + 0.35 * idx) * (1 if idx % 2 == 0 else -1)
            p.save()
            p.translate(cx, cy)
            p.rotate(rot)
            p.translate(-cx, -cy)

            base_a = alpha * (0.65 + 0.35 * self._speak)
            span = 360.0 / seg
            n_arc = int(seg)

            # 1) lapisan glow: busur lebih lebar & redup, memberi kesan
            #    cahaya keluar dari garis (bukan garis datar).
            glow = QColor(255, 46, 24)
            glow.setAlphaF(min(1.0, base_a * 0.38))
            gpen = QPen(glow)
            gpen.setWidthF(max(1.6, side * width * 2.4))
            gpen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(gpen)
            for i in range(n_arc):
                start = i * span + 12.0
                p.drawArc(rect, int(start * 16), int((span - 34.0) * 16))

            # 2) garis inti dengan alpha MERUNCING di kedua ujung busur.
            #    Ujung yang tumpul membuat cincin terlihat seperti potongan
            #    kabel; meruncing membuatnya terbaca sebagai cahaya.
            core = QColor(255, 96, 62)
            pen = QPen(core)
            pen.setWidthF(max(0.8, side * width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            for i in range(n_arc):
                start = i * span + 12.0
                total = span - 34.0
                steps = 9
                for k in range(steps):
                    f0 = k / steps
                    f1 = (k + 1) / steps
                    taper = math.sin(math.pi * (f0 + f1) / 2.0) ** 0.65
                    c = QColor(core)
                    c.setAlphaF(max(0.0, min(1.0, base_a * 1.35 * taper)))
                    pen.setColor(c)
                    p.setPen(pen)
                    p.drawArc(rect, int((start + total * f0) * 16),
                              int((total * (f1 - f0)) * 16))
            p.restore()

            # titik komet di salah satu busur
            ang = math.radians(rot + 40.0 + idx * 55.0)
            kx = cx + math.cos(ang) * r
            ky = cy + math.sin(ang) * r * 0.94
            kr = side * (0.006 + 0.003 * self._speak)
            # komet kecil dan tajam, bukan bulatan buram
            g = QRadialGradient(QPointF(kx, ky), kr * 3)
            c0 = QColor(255, 215, 190)
            c0.setAlphaF(min(1.0, 0.95 * (0.55 + 0.45 * self._speak)))
            g.setColorAt(0.0, c0)
            g.setColorAt(0.35, QColor(255, 120, 80, 150))
            g.setColorAt(1.0, QColor(255, 60, 40, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(kx, ky), kr * 3, kr * 3)
        p.restore()

    def _draw_waveform(self, p: QPainter, cx: float, cy: float,
                       side: float) -> None:
        """Gelombang suara di bawah wajah — bergerak mengikuti level mikrofon."""
        amp = side * 0.014 * (0.25 + 0.9 * self._level + 1.1 * self._speak)
        half = side * 0.215
        y0 = cy + side * 0.430
        pts_l: list[QPointF] = []
        pts_r: list[QPointF] = []
        steps = 64
        for i in range(steps + 1):
            u = i / steps
            x = cx - half + half * 2 * u
            env = math.sin(math.pi * u) ** 1.4
            w = (math.sin(u * 26.0 + self._t * 7.0)
                 + 0.55 * math.sin(u * 13.0 - self._t * 4.3)
                 + 0.30 * math.sin(u * 41.0 + self._t * 9.5))
            y = y0 + w * amp * env
            (pts_l if i % 2 == 0 else pts_r).append(QPointF(x, y))

        path = QPainterPath()
        path.moveTo(cx - half, y0)
        for i in range(steps + 1):
            u = i / steps
            x = cx - half + half * 2 * u
            env = math.sin(math.pi * u) ** 1.4
            w = (math.sin(u * 26.0 + self._t * 7.0)
                 + 0.55 * math.sin(u * 13.0 - self._t * 4.3)
                 + 0.30 * math.sin(u * 41.0 + self._t * 9.5))
            path.lineTo(x, y0 + w * amp * env)

        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        glow = QPen(QColor(255, 80, 50, 42))
        glow.setWidthF(max(1.6, side * 0.008))
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(glow)
        p.drawPath(path)
        core = QPen(QColor(255, 175, 150, 165))
        core.setWidthF(max(0.9, side * 0.0026))
        p.setPen(core)
        p.drawPath(path)
        p.restore()

    def _draw_vignette(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        g = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.62)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(0.72, QColor(0, 0, 0, 40))
        g.setColorAt(1.0, QColor(0, 0, 0, 210))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(self.rect())


# ── uji ─────────────────────────────────────────────────────────────────

def _shot(path: str, state: str, level: float, w: int, h: int) -> int:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    face = Face25DWidget()
    face.resize(w, h)
    face.set_state(state)
    face.set_audio_level(level)
    face.show()
    app.processEvents()
    for _ in range(30):            # biarkan denyut & partikel berkembang
        face._tick()
        app.processEvents()
    img = face.grab().toImage()
    ok = img.save(path)
    print(f"[Face25D] {path} {img.width()}x{img.height()} simpan={ok}")
    face.close()
    return 0 if ok else 1


def _demo() -> int:
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = QWidget()
    win.setWindowTitle("JARVIS - uji wajah 2.5D")
    win.resize(720, 800)
    win.setStyleSheet("background:#05070c;")
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    face = Face25DWidget()
    lay.addWidget(face, 1)
    hint = QLabel("1 IDLE   2 LISTENING   3 THINKING   4 SPEAKING   -   ESC keluar")
    hint.setStyleSheet("color:#7c8aa0; font:12px 'Consolas'; padding:8px;")
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(hint)

    def on_key(ev):
        k = ev.key()
        if k == Qt.Key.Key_Escape:
            win.close()
        elif k in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4):
            face.set_state({Qt.Key.Key_1: "IDLE", Qt.Key.Key_2: "LISTENING",
                            Qt.Key.Key_3: "THINKING",
                            Qt.Key.Key_4: "SPEAKING"}[k])

    win.keyPressEvent = on_key
    win.show()
    return app.exec()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--shot")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--state", default="IDLE")
    ap.add_argument("--level", type=float, default=0.4)
    ap.add_argument("--w", type=int, default=800)
    ap.add_argument("--h", type=int, default=800)
    a = ap.parse_args()
    if a.shot:
        sys.exit(_shot(a.shot, a.state, a.level, a.w, a.h))
    if a.demo:
        sys.exit(_demo())
    print("Pakai --demo, atau --shot out.png --state SPEAKING")
