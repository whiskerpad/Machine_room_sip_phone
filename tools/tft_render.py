#!/usr/bin/env python3
"""
tft_render.py - wifi_ui.py の画面を実機なしで PNG に描き出す (ブログ用スクリーンショット)

wifi_ui.py が 328P に送る描画コマンド (C/R/B/L/T) をそのまま受け取り、
tft_coproc.ino と同じ規則 (Adafruit GFX 標準 5x7 フォント, RGB565, 自動折り返し) で
320x480 のキャンバスに描く。画面関数がタッチ待ち (Screen.wait) に入った時点で保存する。

使い方:  python tools/tft_render.py [出力フォルダ] [倍率]
必要:    Pillow
         tools/glcdfont.c … Adafruit-GFX-Library (BSD License) の glcdfont.c を取得して置く
           https://github.com/adafruit/Adafruit-GFX-Library/blob/master/glcdfont.c
         wifi_ui.py / phone.py はリポジトリ直下のものを読み込む
"""
import json
import os
import re
import sys
import tempfile
import time
import types

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "tft_png")
SCALE = int(sys.argv[2]) if len(sys.argv) > 2 else 2

# ---- ブログに載せるダミー値 (実 IP / SSID / MAC は出さない) ----
DUMMY = {
    "ssid": "Mall_Free_WiFi",
    "ts_ip": "100.64.0.21",
    "aor": "sip:1003@100.64.0.10",
    "ip": {"GENERAL.CONNECTION": "sip-Mall_Free_WiFi", "GENERAL.HWADDR": "B8:27:EB:00:00:00",
           "IP4.ADDRESS": "10.20.0.123/22", "IP4.GATEWAY": "10.20.0.1", "IP4.DNS": "10.20.0.1"},
    "contacts": [{"name": "PC", "ext": "1001"}, {"name": "iPhone", "ext": "1002"}],
    "history": [{"t": "11/02 19:42", "kind": "in", "ext": "1002", "dur": 184},
                {"t": "11/02 18:10", "kind": "out", "ext": "1001", "dur": 62},
                {"t": "11/01 21:05", "kind": "miss", "ext": "1002", "dur": 0},
                {"t": "11/01 20:30", "kind": "in", "ext": "1001", "dur": 15}],
    "nets": [
        dict(ssid="Mall_Free_WiFi", signal=78, security="", chan="6", bssid="", inuse=True),
        dict(ssid="Cafe-Guest", signal=64, security="WPA2", chan="1", bssid="", inuse=False),
        dict(ssid="aterm-xxxxxx-g", signal=55, security="WPA2", chan="11", bssid="", inuse=False),
        dict(ssid="Shop_Staff", signal=47, security="WPA2 WPA3", chan="36", bssid="", inuse=False),
        dict(ssid="Buffalo-G-XXXX", signal=33, security="WPA2", chan="3", bssid="", inuse=False),
        dict(ssid="FREE_SPOT", signal=21, security="", chan="9", bssid="", inuse=False),
    ],
}


# ------------------------------------------------ Adafruit GFX 標準フォント ----
def load_font():
    src = open(os.path.join(HERE, "glcdfont.c"), encoding="utf-8").read()
    body = src[src.index("font[]"):]
    body = body[body.index("{") + 1:body.index("}")]
    return bytes(int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{2}", body))


FONT = load_font()


def rgb(c):
    c = int(c) & 0xFFFF
    return (((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)


class Canvas:
    """ILI9488 (setRotation(0) 320x480) を模した描画先"""

    def __init__(self, w=320, h=480):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h), (0, 0, 0))
        self.dr = ImageDraw.Draw(self.img)

    def fill_rect(self, x, y, w, h, c):
        if w <= 0 or h <= 0:
            return
        self.dr.rectangle([x, y, x + w - 1, y + h - 1], fill=rgb(c))

    def draw_rect(self, x, y, w, h, c):
        if w <= 0 or h <= 0:
            return
        self.dr.rectangle([x, y, x + w - 1, y + h - 1], outline=rgb(c))

    def line(self, x0, y0, x1, y1, c):
        self.dr.line([x0, y0, x1, y1], fill=rgb(c))

    def char(self, x, y, ch, fg, bg, s):
        code = ord(ch)
        if code >= 176:          # Adafruit GFX の cp437 互換ずれ (ASCII のみなので通常は無関係)
            code += 1
        for i in range(5):
            col = FONT[code * 5 + i]
            for j in range(8):
                if col & (1 << j):
                    self.fill_rect(x + i * s, y + j * s, s, s, fg)
                elif bg != fg:
                    self.fill_rect(x + i * s, y + j * s, s, s, bg)
        if bg != fg:
            self.fill_rect(x + 5 * s, y, s, 8 * s, bg)

    def text(self, x, y, s, fg, bg, txt):
        s = max(1, s)
        if bg < 0:
            bg = fg               # setTextColor(fg) のみ = 背景透過
        cx, cy = x, y
        for ch in txt:
            if cx + s * 6 > self.w:   # wrap=true (既定) の折り返し
                cx, cy = 0, cy + s * 8
            self.char(cx, cy, ch, fg, bg, s)
            cx += s * 6

    def exec(self, line):
        """tft_coproc.ino の execLine と同じ解釈"""
        op, rest = line[0], line[1:]
        if op == "T":
            m = re.match(r"\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+) ?(.*)$", rest)
            x, y, s, fg, bg = (int(v, 0) for v in m.groups()[:5])
            self.text(x, y, s, fg, bg, m.group(6))
            return
        a = [int(v, 0) for v in rest.split()]
        if op == "C":
            self.fill_rect(0, 0, self.w, self.h, a[0])
        elif op == "R":
            self.fill_rect(*a[:5])
        elif op == "B":
            self.draw_rect(*a[:5])
        elif op == "L":
            self.line(*a[:5])

    def save(self, path, scale):
        im = self.img if scale == 1 else self.img.resize((self.w * scale, self.h * scale), Image.NEAREST)
        im.save(path)


# ------------------------------------------- wifi_ui.py を実機なしで読み込む ----
WORK = tempfile.mkdtemp(prefix="tftshot_")
sys.modules["captive_lib"] = types.SimpleNamespace(PROBE_URLS={"google": ["http://example/"]})
sys.path.insert(0, ROOT)
import phone as ph          # noqa: E402

ph.CONTACTS_FILE = os.path.join(WORK, "contacts.json")
ph.HISTORY_FILE = os.path.join(WORK, "call_log.json")
json.dump(DUMMY["contacts"], open(ph.CONTACTS_FILE, "w"))
json.dump(DUMMY["history"], open(ph.HISTORY_FILE, "w"))

import wifi_ui as ui        # noqa: E402

ui.ip_info = lambda: dict(DUMMY["ip"])
ui.tailscale_ip = lambda: DUMMY["ts_ip"]
ui.KEEPER.status = {"state": "ONLINE", "time": "19:58:03", "detail": ""}
ph.volume = lambda step: "70%"


class Snap(Exception):
    pass


class FakeDisplay(ui.Display):
    """シリアルの代わりに Canvas へ描く。タッチは台本 (taps) から返す"""

    def __init__(self):
        self.canvas = Canvas()
        self.taps, self.raw_script = [], []
        self.last_tap, self.reset_seen = 0.0, False
        self.cal = ui.DEFAULT_CAL

    def cmd(self, s):
        self.canvas.exec(s)

    def clear_taps(self):
        pass

    def get_raw_tap(self):
        if not self.raw_script:
            raise Snap
        return self.raw_script.pop(0)


class FakePhone:
    def __init__(self):
        self.connected, self.reg, self.aor = True, "OK", DUMMY["aor"]
        self.call, self.muted, self.seq = None, False, 0

    def clear_call(self):
        raise Snap

    def accept(self):
        pass

    hangup = toggle_mute = reregister = accept


SCRIPT = []   # Screen.wait が順に返すキー。尽きたら撮影


def scripted_wait(self, timeout=None):
    if not SCRIPT:
        raise Snap
    return SCRIPT.pop(0)


ui.Screen.wait = scripted_wait
ui.PHONE = FakePhone()

D = FakeDisplay()
SCR = ui.Screen(D)
os.makedirs(OUT, exist_ok=True)
made = []


def shot(name, fn, keys=(), raws=()):
    D.canvas = Canvas()
    D.raw_script = list(raws)
    SCRIPT[:] = list(keys)
    try:
        fn()
    except Snap:
        pass
    path = os.path.join(OUT, name + ".png")
    D.canvas.save(path, SCALE)
    made.append(path)


# ----------------------------------------------------------- 撮影する画面 ----
def boot():
    c = D.canvas                 # 328P 単体の起動画面 (setup())
    c.fill_rect(0, 0, 320, 480, 0)
    c.text(10, 10, 2, 0xFFFF, -1, "Waiting for Pi...")
    raise Snap


def call(state, peer="1002", **kw):
    def f():
        ui.PHONE.muted = kw.pop("muted", False)
        ui.PHONE.call = dict(state=state, peer=peer, **kw)
        ui._call_screen(SCR)
    return f


shot("00_boot", boot)
shot("01_home", lambda: ui.home(SCR))
shot("02_dial", lambda: ui.dialpad(SCR), [("KEY", c) for c in "1002"])
shot("03_history", lambda: ui.history_screen(SCR))
shot("04_call_outgoing", call("RINGING", "1001"))
shot("05_call_incoming", call("INCOMING"))
shot("06_call_established", call("ESTABLISHED", t0=time.monotonic() - 83))
shot("07_call_muted", call("ESTABLISHED", t0=time.monotonic() - 125, muted=True))
shot("08_call_ended", call("CLOSED", dur=184, reason="Connection reset by user"))
shot("09_wifi_list", lambda: ui.draw_list(SCR, DUMMY["nets"], 0, 1) or (_ for _ in ()).throw(Snap))
shot("10_wifi_password", lambda: ui.keyboard(SCR, "Cafe-Guest"),
     [("KEY", 1)] * 3 + [None, ("KEY", 1), None] + [("KEY", 2)] * 3 + [None] + [("KEY", 2)] * 2)
shot("11_wifi_status_online", lambda: ui.status_screen(SCR, DUMMY["ssid"], dict(
    state="ONLINE", time="19:58:03", recipe="mall_agree", detail="auth mall_agree: OK")))
shot("12_wifi_status_portal", lambda: ui.status_screen(SCR, "FREE_SPOT", dict(
    state="PORTAL", time="20:01:44", recipe=None, portal_url="http://10.0.0.1/login",
    forms=[{"method": "POST", "action": "/agree", "fields": [{"tag": "input", "name": "agree"},
                                                              {"tag": "input", "type": "submit"}]}],
    detail="unknown portal, forms=1")))
shot("13_touch_cal", lambda: ui.calibrate(SCR))
# 校正テスト画面: 9点の生値を与え、その後に数回タップした点を表示
nine = [(611, 880), (352, 875), (110, 870), (606, 485), (352, 485), (112, 485), (600, 116), (350, 118), (115, 120)]
shot("14_touch_cal_test", lambda: ui.calibrate(SCR), raws=nine + [(480, 700), (250, 300), (180, 620)])
shot("15_power_off", lambda: ui.message(SCR, "Power", [("Power off?", 2, ui.WHITE)],
                                       [("YES", "YES"), ("NO", "NO")], {"YES": ui.RED}))

# 一覧画像 (4列)
cols = 4
ims = [Image.open(p) for p in made]
w, h = ims[0].size
pad = 16 * SCALE // 2
sheet = Image.new("RGB", (cols * (w + pad) + pad, ((len(ims) + cols - 1) // cols) * (h + pad) + pad), (40, 40, 40))
for i, im in enumerate(ims):
    sheet.paste(im, (pad + (i % cols) * (w + pad), pad + (i // cols) * (h + pad)))
sheet.thumbnail((2400, 2400))
sheet.save(os.path.join(OUT, "contact_sheet.png"))
print("\n".join(made))
