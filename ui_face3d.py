"""Wajah 3D JARVIS — widget OpenGL mandiri (QOpenGLWidget).

Kenapa modul terpisah:
  * bisa dirender & difoto tanpa menjalankan aplikasi penuh (`--shot`),
  * tidak menambah risiko ke `ui.py` (9.600 baris) selama belum dipakai,
  * satu tempat untuk seluruh pipeline: mesh, shader, bloom, partikel.

Isi pipeline:
  1. Mesh wajah/helm prosedural (numpy) + crest di tengah dahi.
  2. Shader logam gelap: Fresnel rim + specular + aksen emissive.
  3. Dua mata menyala; intensitas mengikuti level suara.
  4. Bloom: bright-pass → blur horizontal → blur vertikal → komposit aditif.
  5. Partikel cahaya keluar saat berbicara ("anima cahaya").

API yang dipakai `ui.py` nanti:
    w = Face3DWidget(accent="#00fff4")
    w.set_state("LISTENING" | "THINKING" | "SPEAKING" | "IDLE")
    w.set_audio_level(0.0 .. 1.0)

Uji tampilan tanpa membuka jendela:
    python ui_face3d.py --shot out.png --state speaking --t 1.6
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from OpenGL import GL
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QSurfaceFormat
from PyQt6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader,
                            QOpenGLShaderProgram,
                            QOpenGLVertexArrayObject)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

_DEBUG = bool(os.environ.get("FACE3D_DEBUG"))


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[Face3D] {msg}", flush=True)

# ── shader ──────────────────────────────────────────────────────────────────

VS_HEAD = """
#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNrm;
layout(location=2) in vec2 aUV;
uniform mat4 uMVP;
uniform mat4 uModel;
uniform float uTime;
out vec3 vPos;
out vec3 vNrm;
out vec2 vUV;
void main() {
    vec3 p = aPos;
    // napas halus: seluruh wajah bergerak sangat sedikit
    p *= 1.0 + 0.006 * sin(uTime * 1.6);
    vPos = p;
    vNrm = aNrm;
    vUV  = aUV;
    gl_Position = uMVP * vec4(p, 1.0);
}
"""

FS_HEAD = """
#version 330 core
in vec3 vPos;
in vec3 vNrm;
in vec2 vUV;
uniform vec3  uAccent;
uniform vec3  uEyePos;
uniform float uTime;
uniform float uLevel;      // 0..1 level suara
uniform float uSpeak;      // 0..1 sedang berbicara
uniform float uClay;       // 1 = bentuk saja, 2 = normal (diagnosa)
out vec4 fragColor;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

void main() {
    // potong di luar siluet topeng (rumus sama dengan build_head)
    float xs = 0.95 * (1.0 - 0.28 * (0.5 * (1.0 + tanh(-(vPos.y + 0.30) * 1.2 * 2.2))));
    float rr = length(vec2(vPos.x / xs, vPos.y / 1.28));
    if (rr > 1.0) discard;

    vec3 N = normalize(vNrm);
    vec3 V = normalize(vec3(0.0, 0.0, 3.2) - vPos);
    vec3 L = normalize(vec3(-0.55, 0.75, 1.15));

    // smoothstep, bukan max(): max() membuat garis batas cahaya yang keras
    float diff = smoothstep(-0.25, 0.65, dot(N, L));
    float spec = pow(max(dot(reflect(-L, N), V), 0.0), 14.0);
    float fres = pow(1.0 - max(dot(N, V), 0.0), 3.2);

    // dasar: logam gelap kebiruan
    vec3 base = vec3(0.085, 0.095, 0.120);
    base += vec3(0.42, 0.47, 0.56) * diff * 0.95;
    base += vec3(0.80, 0.86, 1.00) * spec * 0.30;

    // rim: garis tipis di tepi siluet
    vec3 rim = uAccent * fres * (0.22 + 0.55 * uLevel);

    // alur crest: pita vertikal di tengah dahi, menyala
    // garis energi mengikuti tulang pipi (bukan pita di tengah wajah)
    float band = smoothstep(0.075, 0.0, abs(abs(vUV.x - 0.5) - 0.31));
    band *= smoothstep(0.34, 0.46, vUV.y) * smoothstep(0.72, 0.56, vUV.y);
    vec3 glow = uAccent * band * (0.04 + 0.38 * uSpeak + 0.10 * uLevel);

    // retakan halus: tekstur permukaan supaya tidak terlihat plastik
    float n = hash(floor(vUV * vec2(180.0, 240.0)));
    base *= 0.96 + 0.07 * n;

    vec3 col = base + rim + glow;

    // (kilau global dari satu titik dibuang: uEyePos ada di tengah wajah,
    //  sehingga menimbulkan pita vertikal di garis tengah. Cahaya mata sudah
    //  ditangani oleh quad mata yang menyala.)

    if (uClay > 1.5) { fragColor = vec4(N * 0.5 + 0.5, 1.0); return; }
    col = mix(col, vec3(diff * 0.85 + 0.08), uClay);
    fragColor = vec4(col, 1.0);
}
"""

VS_FLAT = """
#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec2 aUV;
uniform mat4 uMVP;
out vec2 vUV;
void main() {
    vUV = aUV;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

FS_EYE = """
#version 330 core
in vec2 vUV;
uniform vec3  uAccent;
uniform float uLevel;
uniform float uSpeak;
uniform float uTime;
out vec4 fragColor;
void main() {
    vec2 p = vUV * 2.0 - 1.0;
    float r = length(p);
    // bentuk mata: lonjong menyempit ke tepi
    float shape = smoothstep(1.0, 0.55, r);
    float core  = exp(-r * 3.4);
    float pulse = 0.72 + 0.28 * sin(uTime * 6.0);
    float inten = (0.55 + 1.1 * uLevel + 0.7 * uSpeak) * pulse;
    vec3 col = uAccent * (shape * 0.55 + core * 1.8) * inten;
    fragColor = vec4(col, shape * 0.95);
}
"""

FS_GLOW = """
#version 330 core
in vec2 vUV;
uniform vec3  uColor;
uniform float uAlpha;
out vec4 fragColor;
void main() {
    vec2 p = vUV * 2.0 - 1.0;
    float r = length(p);
    float a = exp(-r * 4.2) * uAlpha;
    fragColor = vec4(uColor * a, a);
}
"""

VS_PART = """
#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in float aAge;
uniform mat4 uMVP;
uniform float uSize;
out float vAge;
void main() {
    vAge = aAge;
    gl_Position = uMVP * vec4(aPos, 1.0);
    gl_PointSize = uSize * (1.0 - aAge) + 1.0;
}
"""

FS_PART = """
#version 330 core
in float vAge;
uniform vec3 uAccent;
out vec4 fragColor;
void main() {
    vec2 p = gl_PointCoord * 2.0 - 1.0;
    float r = length(p);
    float a = exp(-r * 3.6) * (1.0 - vAge);
    fragColor = vec4(uAccent * a * 1.7, a);
}
"""

FS_BRIGHT = """
#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform float uThresh;
out vec4 fragColor;
void main() {
    vec3 c = texture(uTex, vUV).rgb;
    float l = max(max(c.r, c.g), c.b);
    float k = smoothstep(uThresh, uThresh + 0.35, l);
    fragColor = vec4(c * k, 1.0);
}
"""

FS_BLUR = """
#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform vec2 uDir;      // (1/w, 0) atau (0, 1/h)
out vec4 fragColor;
void main() {
    vec3 sum = vec3(0.0);
    float wsum = 0.0;
    for (int i = -6; i <= 6; ++i) {
        float w = exp(-float(i * i) / 18.0);
        sum  += texture(uTex, vUV + uDir * float(i) * 1.4).rgb * w;
        wsum += w;
    }
    fragColor = vec4(sum / wsum, 1.0);
}
"""

FS_COMPOSITE = """
#version 330 core
in vec2 vUV;
uniform sampler2D uScene;
uniform sampler2D uBloom;
uniform float uBloomAmt;
out vec4 fragColor;
void main() {
    vec3 s = texture(uScene, vUV).rgb;
    vec3 b = texture(uBloom, vUV).rgb;
    fragColor = vec4(s + b * uBloomAmt, 1.0);
}
"""


# ── geometri ────────────────────────────────────────────────────────────────

def _gauss(d: np.ndarray, s: float) -> np.ndarray:
    return np.exp(-(d * d) / (2.0 * s * s))


def build_head(nu: int = 170, nv: int = 210) -> np.ndarray:
    """Topeng prosedural berbasis PELAT, bukan bola yang didorong.

    Percobaan pertama memakai ellipsoid + gaussian; hasilnya selalu terbaca
    sebagai "telur" karena bola tidak punya topologi wajah. Pendekatan ini
    menyusun ketinggian z(x, y) dari bentuk-bentuk eksplisit — alis, rongga
    mata, batang hidung, tulang pipi, rahang, dagu — lalu memotong siluetnya
    di shader (discard di luar radius), sehingga yang terlihat adalah topeng.

    Semua mask dibuat halus (tanh), bukan np.clip: potongan keras pada
    permukaan menghasilkan pita terang mendatar yang terlihat seperti artefak.
    """
    u = np.linspace(0.0, 1.0, nu)
    v = np.linspace(0.0, 1.0, nv)
    U, V = np.meshgrid(u, v)

    X = (U - 0.5) * 2.0          # -1 .. 1
    Y = (V - 0.5) * 2.0

    x = X * 0.95
    y = Y * 1.28

    def soft(t):
        return 0.5 * (1.0 + np.tanh(t * 2.2))

    # Radius siluet: ELIPS halus dengan dagu menyempit.
    # Percobaan sebelumnya memakai superellipse (eksponen 1.7): di puncak
    # kepala bentuk itu membentuk "pinch" dan lipatan di situ memancarkan
    # garis gelap ke bawah wajah. Elips tidak punya titik patah.
    xs = 0.95 * (1.0 - 0.28 * soft(-(y + 0.30) * 1.2))
    r = np.sqrt((x / xs) ** 2 + (y / 1.28) ** 2)

    # pelat melengkung: tinggi maksimum di tengah, nol di tepi
    z = 0.62 * np.cos(np.clip(r, 0.0, 1.0) * math.pi * 0.5) ** 1.2

    # alis: rak mendatar, dengan lekuk di pangkal hidung
    brow = np.exp(-((y - 0.30) / 0.085) ** 2) * np.exp(-(x / 0.62) ** 4)
    glabella = np.exp(-(x / 0.065) ** 2) * np.exp(-((y - 0.30) / 0.10) ** 2)
    z += 0.115 * brow - 0.042 * glabella

    # rongga mata: dua cekungan almond
    for s_ in (-1.0, 1.0):
        z -= 0.150 * np.exp(-(((x - s_ * 0.335) / 0.150) ** 2
                              + ((y - 0.075) / 0.080) ** 2))

    # batang hidung. Dibatasi jendela-y: tanpa ini punggung hidung menjulur
    # sampai dahi dan tampak sebagai garis terang/gelap di tengah wajah.
    nose_win = soft(0.08 - y) * soft(y + 0.55)
    z += 0.165 * np.exp(-(x / 0.090) ** 2) * nose_win
    z += 0.040 * np.exp(-((x / 0.115) ** 2 + ((y + 0.235) / 0.085) ** 2))

    # tulang pipi & cekungan pipi
    for s_ in (-1.0, 1.0):
        z += 0.050 * np.exp(-(((x - s_ * 0.58) / 0.20) ** 2
                              + ((y + 0.02) / 0.26) ** 2))
        z -= 0.055 * np.exp(-(((x - s_ * 0.46) / 0.17) ** 2
                              + ((y + 0.42) / 0.18) ** 2))

    # rahang & dagu
    z -= 0.10 * soft(-(y + 0.62) * 1.4) * (1.0 - np.exp(-(x / 0.55) ** 2))
    z += 0.055 * np.exp(-((x / 0.17) ** 2 + ((y + 0.98) / 0.16) ** 2))

    # (crest tipis dibuang: sigma 0.045 membuat normal berbalik dan muncul
    #  pita hitam tepat di tengah wajah)

    # bibir pelat: tepi melengkung ke belakang supaya ada ketebalan
    z -= 0.13 * soft((r - 0.86) * 3.0)

    pos = np.stack([x, y, z], axis=-1).astype(np.float32)
    uv = np.stack([U, V], axis=-1).astype(np.float32)

    # Normal ANALITIK dari turunan z, bukan cross-product antar-vertex.
    # Di puncak siluet semua vertex bertumpuk (r>1), sehingga cross-product
    # menghasilkan normal sampah yang menyebar lewat penghalusan dan muncul
    # sebagai garis gelap memancar dari ubun-ubun. Turunan z selalu terdefinisi.
    dx = float(x[0, 1] - x[0, 0])
    dy = float(y[1, 0] - y[0, 0])
    dzdx = np.gradient(z, axis=1) / max(dx, 1e-6)
    dzdy = np.gradient(z, axis=0) / max(dy, 1e-6)
    one = np.ones_like(z)
    nrm = np.stack([-dzdx, -dzdy, one], axis=-1)
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-6)
    nrm = nrm.astype(np.float32)

    # Hanya sel yang seluruh sudutnya di dalam siluet DAN luasnya cukup.
    # Di puncak, baris teratas menyusut jadi satu titik; sel di sekitarnya
    # menjadi segitiga tipis yang menghasilkan garis gelap memancar.
    inside = r <= 1.0
    tris = []
    for j in range(nv - 1):
        for i in range(nu - 1):
            if not (inside[j, i] and inside[j, i + 1]
                    and inside[j + 1, i] and inside[j + 1, i + 1]):
                continue
            p00 = pos[j, i]
            p01 = pos[j, i + 1]
            p10 = pos[j + 1, i]
            p11 = pos[j + 1, i + 1]
            area = (np.linalg.norm(np.cross(p01 - p00, p10 - p00))
                    + np.linalg.norm(np.cross(p11 - p01, p11 - p10)))
            if area < 1e-5:
                continue
            a = j * nu + i
            b = a + 1
            c = a + nu
            d = c + 1
            tris += [(a, b, c), (b, d, c)]
    if not tris:
        return np.zeros((0, 8), dtype=np.float32)
    idx = np.array(tris, dtype=np.uint32)

    fp = pos.reshape(-1, 3)[idx].reshape(-1, 3)
    fn = nrm.reshape(-1, 3)[idx].reshape(-1, 3)
    fu = uv.reshape(-1, 2)[idx].reshape(-1, 2)
    return np.hstack([fp, fn, fu]).astype(np.float32)


def build_quad() -> np.ndarray:
    return np.array([
        -1, -1, 0, 0, 0,
        1, -1, 0, 1, 0,
        1, 1, 0, 1, 1,
        -1, -1, 0, 0, 0,
        1, 1, 0, 1, 1,
        -1, 1, 0, 0, 1,
    ], dtype=np.float32)


# ── widget ──────────────────────────────────────────────────────────────────

class Face3DWidget(QOpenGLWidget):
    """Wajah 3D reaktif. Aman dipakai sebagai widget HUD."""

    def __init__(self, parent=None, accent: str = "#00fff4"):
        super().__init__(parent)
        self._accent = QColor(accent)
        self._state = "IDLE"
        self._level = 0.0
        self._speak = 0.0
        self._t = 0.0
        self._ready = False
        self._progs: dict[str, QOpenGLShaderProgram] = {}
        self._bufs: dict[str, QOpenGLBuffer] = {}
        self._fbo = {}
        self._parts = np.zeros((220, 4), dtype=np.float32)   # x,y,z,age
        self._parts[:, 3] = 1.0
        self._ptimer = QTimer(self)
        self._ptimer.timeout.connect(self._tick)
        self._ptimer.start(16)

    # API untuk ui.py
    def set_state(self, state: str) -> None:
        self._state = (state or "IDLE").upper()

    def set_audio_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level or 0.0)))

    # animasi
    def _tick(self) -> None:
        self._t += 0.016
        target = 1.0 if self._state == "SPEAKING" else 0.0
        self._speak += (target - self._speak) * 0.10
        if self._state == "LISTENING":
            self._level = max(self._level * 0.94, 0.10)
        self.update()

    def _spawn(self, n: int) -> None:
        idx = np.where(self._parts[:, 3] >= 1.0)[0]
        if len(idx) == 0:
            return
        idx = idx[:n]
        k = len(idx)
        ang = np.random.uniform(0, math.pi * 2, k)
        rad = np.random.uniform(0.10, 0.42, k)
        self._parts[idx, 0] = np.cos(ang) * rad
        self._parts[idx, 1] = np.sin(ang) * rad * 0.8 - 0.05
        self._parts[idx, 2] = 0.95
        self._parts[idx, 3] = 0.0

    # ── GL ──────────────────────────────────────────────────────────────
    def initializeGL(self) -> None:
        # Kesalahan GL TIDAK boleh mematikan JARVIS: PyQt6 membatalkan proses
        # bila exception lolos dari virtual method ini. Semua kegagalan
        # ditangkap, dilaporkan sekali, lalu widget berhenti menggambar.
        try:
            self._init_gl()
        except Exception as e:                   # noqa: BLE001
            print(f"[Face3D] inisialisasi gagal, widget dimatikan: {e}")
            self._failed = True
            self._ready = False

    def _init_gl(self) -> None:
        _dbg("init: mulai")
        # Profil core WAJIB punya VAO; tanpa ini glDrawArrays = 1282.
        self._vao = QOpenGLVertexArrayObject()
        self._vao.create()
        self._vao.bind()
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        # profil core mengabaikan gl_PointSize tanpa ini -> partikel 1 px
        GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        _dbg("init: kompilasi head")
        self._progs["head"] = self._prog(VS_HEAD, FS_HEAD, "head")
        self._progs["eye"] = self._prog(VS_FLAT, FS_EYE, "eye")
        self._progs["glow"] = self._prog(VS_FLAT, FS_GLOW, "glow")
        self._progs["part"] = self._prog(VS_PART, FS_PART, "part")
        self._progs["bright"] = self._prog(VS_FLAT, FS_BRIGHT, "bright")
        self._progs["blur"] = self._prog(VS_FLAT, FS_BLUR, "blur")
        self._progs["comp"] = self._prog(VS_FLAT, FS_COMPOSITE, "comp")

        _dbg("init: bangun mesh")
        self._upload("head", build_head(), 8)
        self._upload("quad", build_quad(), 5)
        pb = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        pb.create()
        pb.bind()
        pb.allocate(self._parts.tobytes(), self._parts.nbytes)
        pb.release()
        self._bufs["part"] = pb
        _dbg("init: selesai")
        self._ready = True

    def _prog(self, vs: str, fs: str, name: str) -> QOpenGLShaderProgram:
        p = QOpenGLShaderProgram()
        okv = p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vs)
        okf = p.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, fs)
        okl = p.link()
        if not (okv and okf and okl):
            print(f"[Face3D] shader '{name}' gagal:\n{p.log()}")
        return p

    def _upload(self, name: str, data: np.ndarray, stride: int) -> None:
        b = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        b.create()
        b.bind()
        b.allocate(data.tobytes(), data.nbytes)
        b.release()
        self._bufs[name] = b

    def resizeGL(self, w: int, h: int) -> None:
        self._fbo = {}

    def _ensure_fbo(self):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        w = max(2, int(self.width() * self.devicePixelRatioF()))
        h = max(2, int(self.height() * self.devicePixelRatioF()))
        if self._fbo.get("size") == (w, h):
            return
        f = QOpenGLFramebufferObjectFormat()
        f.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
        self._fbo = {
            "size": (w, h),
            "scene": QOpenGLFramebufferObject(w, h, f),
            "half": QOpenGLFramebufferObject(max(2, w // 2), max(2, h // 2), f),
            "blur": QOpenGLFramebufferObject(max(2, w // 2), max(2, h // 2), f),
        }

    def paintGL(self) -> None:
        if not self._ready or getattr(self, "_failed", False):
            return
        try:
            self._paint_gl()
        except Exception as e:                   # noqa: BLE001
            print(f"[Face3D] gambar gagal, widget dimatikan: {e}")
            self._failed = True
            self._ready = False

    def _paint_gl(self) -> None:
        from PyQt6.QtGui import QMatrix4x4, QVector3D

        _dbg("paint: mulai")
        self._ensure_fbo()
        _dbg("paint: fbo siap")
        scene = self._fbo["scene"]
        scene.bind()
        GL.glViewport(0, 0, scene.width(), scene.height())
        GL.glClearColor(0.008, 0.011, 0.020, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

        proj = QMatrix4x4()
        proj.perspective(38.0, self.width() / max(1, self.height()), 0.1, 40.0)
        view = QMatrix4x4()
        view.translate(0.0, 0.0, -3.55)
        view.rotate(-6.0 + 2.2 * math.sin(self._t * 0.35), 1, 0, 0)
        view.rotate(7.5 * math.sin(self._t * 0.22), 0, 1, 0)
        mvp = proj * view

        acc = np.array([self._accent.redF(), self._accent.greenF(),
                        self._accent.blueF()], dtype=np.float32)

        # 1) kepala
        ph = self._progs["head"]
        ph.bind()
        ph.setUniformValue("uMVP", mvp)
        ph.setUniformValue("uTime", float(self._t))
        ph.setUniformValue("uLevel", float(self._level))
        ph.setUniformValue("uSpeak", float(self._speak))
        ph.setUniformValue("uAccent", QVector3D(*[float(v) for v in acc]))
        ph.setUniformValue("uEyePos", QVector3D(0.0, 0.13, 0.78))
        _clay = 0.0
        if os.environ.get("FACE3D_NRM"):
            _clay = 2.0
        elif os.environ.get("FACE3D_CLAY"):
            _clay = 1.0
        ph.setUniformValue("uClay", _clay)
        b = self._bufs["head"]
        b.bind()
        ph.enableAttributeArray(0)
        ph.setAttributeBuffer(0, GL.GL_FLOAT, 0, 3, 8 * 4)
        ph.enableAttributeArray(1)
        ph.setAttributeBuffer(1, GL.GL_FLOAT, 3 * 4, 3, 8 * 4)
        ph.enableAttributeArray(2)
        ph.setAttributeBuffer(2, GL.GL_FLOAT, 6 * 4, 2, 8 * 4)
        _dbg("paint: gambar kepala")
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, b.size() // (8 * 4))
        _dbg("paint: kepala ok")
        b.release()

        # 2) mata (aditif)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE)
        pe = self._progs["eye"]
        pe.bind()
        pe.setUniformValue("uAccent", QVector3D(*[float(v) for v in acc]))
        pe.setUniformValue("uLevel", float(self._level))
        pe.setUniformValue("uSpeak", float(self._speak))
        pe.setUniformValue("uTime", float(self._t))
        q = self._bufs["quad"]
        q.bind()
        pe.enableAttributeArray(0)
        pe.setAttributeBuffer(0, GL.GL_FLOAT, 0, 3, 5 * 4)
        pe.enableAttributeArray(1)
        pe.setAttributeBuffer(1, GL.GL_FLOAT, 3 * 4, 2, 5 * 4)
        for sx in (-1.0, 1.0):
            m = QMatrix4x4(mvp)
            m.translate(sx * 0.335, 0.075, 0.430)
            m.scale(0.150, 0.050, 1.0)
            pe.setUniformValue("uMVP", m)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)

        # 3) partikel cahaya saat berbicara
        if self._speak > 0.05:
            self._spawn(max(1, int(6 * self._speak)))
        self._parts[:, 3] = np.minimum(1.0, self._parts[:, 3] + 0.018)
        self._parts[:, 2] += 0.012
        self._parts[:, 0] *= 1.012
        self._parts[:, 1] *= 1.012
        pb = self._bufs["part"]
        pb.bind()
        pb.write(0, self._parts.tobytes(), self._parts.nbytes)
        pp = self._progs["part"]
        pp.bind()
        pp.setUniformValue("uMVP", mvp)
        pp.setUniformValue("uSize", float(9.0 + 10.0 * self._speak))
        pp.setUniformValue("uAccent", QVector3D(*[float(v) for v in acc]))
        pp.enableAttributeArray(0)
        pp.setAttributeBuffer(0, GL.GL_FLOAT, 0, 3, 4 * 4)
        pp.enableAttributeArray(1)
        pp.setAttributeBuffer(1, GL.GL_FLOAT, 3 * 4, 1, 4 * 4)
        GL.glDrawArrays(GL.GL_POINTS, 0, len(self._parts))
        pb.release()

        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        _dbg("paint: scene selesai")
        scene.release()

        _dbg("paint: bloom")
        # 4) bloom
        half = self._fbo["half"]
        blur = self._fbo["blur"]
        qb = self._bufs["quad"]

        half.bind()
        GL.glViewport(0, 0, half.width(), half.height())
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        pb2 = self._progs["bright"]
        pb2.bind()
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, scene.texture())
        pb2.setUniformValue("uTex", 0)
        pb2.setUniformValue("uThresh", 0.62)
        qb.bind()
        pb2.enableAttributeArray(0)
        pb2.setAttributeBuffer(0, GL.GL_FLOAT, 0, 3, 5 * 4)
        pb2.enableAttributeArray(1)
        pb2.setAttributeBuffer(1, GL.GL_FLOAT, 3 * 4, 2, 5 * 4)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
        half.release()

        pbl = self._progs["blur"]
        for direction, dst, src in ((1, blur, half), (0, half, blur)):
            dst.bind()
            GL.glViewport(0, 0, dst.width(), dst.height())
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            pbl.bind()
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, src.texture())
            pbl.setUniformValue("uTex", 0)
            pbl.setUniformValue("uDir",
                                float(direction) / dst.width(),
                                float(1 - direction) / dst.height())
            qb.bind()
            pbl.enableAttributeArray(0)
            pbl.setAttributeBuffer(0, GL.GL_FLOAT, 0, 3, 5 * 4)
            pbl.enableAttributeArray(1)
            pbl.setAttributeBuffer(1, GL.GL_FLOAT, 3 * 4, 2, 5 * 4)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            dst.release()

        _dbg("paint: komposit")
        # 5) salin scene ke layar (blit, tanpa shader) lalu tambahkan bloom
        _dbg("paint: blit")
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, scene.handle())
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER,
                             self.defaultFramebufferObject())
        GL.glBlitFramebuffer(0, 0, scene.width(), scene.height(),
                             0, 0, int(self.width() * self.devicePixelRatioF()),
                             int(self.height() * self.devicePixelRatioF()),
                             GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER,
                             self.defaultFramebufferObject())

        GL.glViewport(0, 0, int(self.width() * self.devicePixelRatioF()),
                      int(self.height() * self.devicePixelRatioF()))
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)      # bloom ditambahkan
        pc = self._progs["comp"]
        pc.bind()
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, half.texture())
        pc.setUniformValue("uScene", 0)
        pc.setUniformValue("uBloom", 0)
        pc.setUniformValue("uBloomAmt", 0.85 + 0.6 * self._speak)
        qb.bind()
        pc.enableAttributeArray(0)
        pc.setAttributeBuffer(0, GL.GL_FLOAT, 0, 3, 5 * 4)
        pc.enableAttributeArray(1)
        pc.setAttributeBuffer(1, GL.GL_FLOAT, 3 * 4, 2, 5 * 4)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        _dbg("paint: selesai")


def _configure_surface() -> None:
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


def _shot(path: str, state: str, t: float, w: int, h: int) -> int:
    from PyQt6.QtWidgets import QApplication
    _configure_surface()
    app = QApplication(sys.argv[:1])
    widget = Face3DWidget(accent="#00fff4")
    widget.resize(w, h)
    widget.set_state(state)
    widget.set_audio_level(0.55 if state == "LISTENING" else 0.25)
    widget.show()
    app.processEvents()
    widget._t = t
    widget._speak = 1.0 if state == "SPEAKING" else 0.0
    for _ in range(40):
        widget._tick()
        app.processEvents()
    img = widget.grabFramebuffer()
    ok = img.save(path)
    # Diagnosa: simpan juga isi FBO scene (tanpa komposit) supaya terlihat
    # apakah masalahnya di render 3D atau di tahap komposit.
    try:
        scene = widget._fbo.get("scene")
        if scene is not None:
            scene.toImage().save(path.replace(".png", "_scene.png"))
            half = widget._fbo.get("half")
            if half is not None:
                half.toImage().save(path.replace(".png", "_bloom.png"))
            print("[Face3D] scene & bloom disimpan untuk diagnosa")
    except Exception as e:                       # noqa: BLE001
        print("[Face3D] gagal simpan diagnosa:", e)
    print(f"[Face3D] {path} {img.width()}x{img.height()} simpan={ok}")
    widget.close()
    return 0 if ok else 1


def _demo(accent: str) -> int:
    """Jendela uji: tekan 1..4 untuk ganti keadaan, ESC keluar."""
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    _configure_surface()
    app = QApplication(sys.argv[:1])
    win = QWidget()
    win.setWindowTitle("JARVIS - uji wajah 3D")
    win.resize(760, 820)
    win.setStyleSheet("background:#05070c;")
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    face = Face3DWidget(accent=accent)
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
    ap.add_argument("--accent", default="#00fff4")
    ap.add_argument("--state", default="IDLE")
    ap.add_argument("--t", type=float, default=1.4)
    ap.add_argument("--w", type=int, default=900)
    ap.add_argument("--h", type=int, default=900)
    a = ap.parse_args()
    if a.shot:
        sys.exit(_shot(a.shot, a.state, a.t, a.w, a.h))
    if a.demo:
        sys.exit(_demo(a.accent))
    print("Pakai --demo untuk melihat langsung, atau --shot out.png untuk uji.")
