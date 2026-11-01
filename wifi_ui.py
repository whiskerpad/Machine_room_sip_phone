#!/usr/bin/env python3
"""
wifi_ui.py - TFT(ATmega328P 表示コプロセッサ)で Wi-Fi AP を選んで接続し、
             キャプティブポータルを検出してポータルのHTMLをログ保存する。

前提: Raspberry Pi OS (Bookworm 以降 / NetworkManager), python3-serial
実行: sudo python3 wifi_ui.py      (nmcli の接続操作に root 権限を使う)
環境変数: TFT_PORT (既定 /dev/serial0), WIFI_IFACE (既定 wlan0)
"""
import datetime
import json
import os
import subprocess
import sys
import time

PORT = os.environ.get("TFT_PORT", "/dev/serial0")
BAUD = 250000
IFACE = os.environ.get("WIFI_IFACE", "wlan0")
W, H = 320, 480
# タッチ補正: 328P から ADC 生値 (A rx ry) を受け、軸ごとの折れ線で画面座標へ変換する。
# 校正データ {"x": [[raw, screen], ...], "y": [...]} を CAL 画面(9点)で作り保存する。
CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "touch_cal.json")
# 未校正時の既定値: 09-27 に Z コマンドで実測した生値 (端・中央・端) から作成
DEFAULT_CAL = {"x": [[611, 5], [352, 160], [110, 315]], "y": [[880, 5], [485, 240], [116, 475]]}


def interp(knots, v):
    """knots=[[raw, scr],...] の折れ線で v を変換(範囲外は端の区間で外挿)"""
    k = sorted(knots)
    i = 1
    while i < len(k) - 1 and v > k[i][0]:
        i += 1
    (r0, s0), (r1, s1) = k[i - 1], k[i]
    return s0 + (v - r0) * (s1 - s0) / (r1 - r0)


def valid_cal(c):
    for axis in ("x", "y"):
        k = sorted(c[axis])
        if len(k) < 2:
            return False
        for (r0, s0), (r1, s1) in zip(k, k[1:]):
            if r1 - r0 < 30:
                return False
        d = [s1 - s0 for (_, s0), (_, s1) in zip(k, k[1:])]
        if not (all(v > 0 for v in d) or all(v < 0 for v in d)):
            return False
    return True


# --- 9点メッシュ補正: 3x3 の実測点を三角形8枚に分割し、三角形ごとの一次変換で写す ---
# (X のずれが Y 位置で変わる歪みにも追従し、9点では誤差0になる)
def mesh_tris(pts):
    """pts: 行優先 3x3 の [rx, ry, sx, sy]。三角形の頂点リストを返す"""
    tris = []
    for r in range(2):
        for c in range(2):
            a, b = pts[r * 3 + c], pts[r * 3 + c + 1]
            d, e = pts[(r + 1) * 3 + c], pts[(r + 1) * 3 + c + 1]
            tris += [(a, b, e), (a, e, d)]
    return tris


def _area(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])


def mesh_valid(pts):
    if len(pts) != 9:
        return False
    signs = set()
    for t in mesh_tris(pts):
        a = _area(*t)
        if abs(a) < 500:  # 点が近すぎる/一直線
            return False
        signs.add(a > 0)
    return len(signs) == 1  # 折れ返し(順序違い)がない


def mesh_map(pts, rx, ry):
    best = None
    for p, q, r in mesh_tris(pts):
        a = _area(p, q, r)
        l1 = _area((rx, ry), q, r) / a
        l2 = _area(p, (rx, ry), r) / a
        l3 = 1 - l1 - l2
        score = min(l1, l2, l3)  # >=0 なら三角形の内側
        if best is None or score > best[0]:
            best = (score, l1, l2, l3, p, q, r)
    _, l1, l2, l3, p, q, r = best
    return (l1 * p[2] + l2 * q[2] + l3 * r[2], l1 * p[3] + l2 * q[3] + l3 * r[3])


def load_cal():
    try:
        with open(CAL_FILE, encoding="utf-8") as f:
            c = json.load(f)
        if "pts" in c:
            pts = [[float(v) for v in p] for p in c["pts"]]
            if mesh_valid(pts):
                return {"pts": pts}
        else:
            c = {a: [[float(r), float(v)] for r, v in c[a]] for a in ("x", "y")}
            if valid_cal(c):
                return c
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_CAL


LOGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captive_logs")
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")

# RGB565
BLACK, WHITE = 0x0000, 0xFFFF
GRAY, DGRAY = 0x8410, 0x2104
NAVY, BLUE = 0x000F, 0x041F
GREEN, RED, YELLOW, CYAN = 0x07E0, 0xF800, 0xFFE0, 0x07FF


# ---------------------------------------------------------------- 表示 ----
def ascii_only(s):
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(s))


class Display:
    """328P とのシリアル通信。コマンドは ACK(K) 待ちで1行ずつ送る。"""

    def __init__(self, port):
        import serial  # python3-serial
        self.ser = serial.Serial(port, BAUD, timeout=0)
        self.rx = bytearray()
        self.taps = []
        self.last_tap = 0.0
        self.reset_seen = False
        self.cal = load_cal()
        self._sync()
        for i in range(3):  # 起動直後の取りこぼし対策で数回試す
            try:
                self.cmd("M 1")  # タッチは生値で受ける
                break
            except IOError:
                if i == 2:
                    raise
                self._sync()

    def to_screen(self, rx, ry, cal=None):
        c = cal or self.cal
        if "pts" in c:
            fx, fy = mesh_map(c["pts"], rx, ry)
        else:
            fx, fy = interp(c["x"], rx), interp(c["y"], ry)
        x, y = int(round(fx)), int(round(fy))
        return min(W - 1, max(0, x)), min(H - 1, max(0, y))

    def after_reset(self):
        """328P 再起動後に生値モードを再設定"""
        self.reset_seen = False
        self.cmd("M 1")

    def _pump(self, wait):
        """最大 wait 秒受信し、完成した行のリストを返す"""
        end = time.monotonic() + wait
        lines = []
        while True:
            n = self.ser.in_waiting
            if n:
                self.rx += self.ser.read(n)
                while b"\n" in self.rx:
                    raw, _, rest = bytes(self.rx).partition(b"\n")
                    self.rx = bytearray(rest)
                    s = raw.decode("ascii", "replace").strip()
                    if s:
                        lines.append(s)
            if lines or time.monotonic() >= end:
                return lines
            time.sleep(0.003)

    def _dispatch(self, line):
        """受信行を処理。ACK なら True"""
        if line == "K":
            return True
        if line.startswith(("A ", "D ")):
            try:
                a, b = map(int, line[2:].split())
            except ValueError:
                return False
            if line[0] == "A":
                x, y = self.to_screen(a, b)
                raw = (a, b)
            else:  # 旧ファームウェア(画面座標)
                x, y, raw = a, b, None
            now = time.monotonic()
            if now - self.last_tap > 0.25:
                self.taps.append((x, y, raw))
            self.last_tap = now
        elif line.startswith("READY"):
            self.reset_seen = True  # 328P が再起動した → 画面再描画が必要
        return False

    def _sync(self):
        for _ in range(60):
            self.ser.reset_input_buffer()
            self.rx.clear()
            self.ser.write(b"P\n")
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                if any([self._dispatch(l) for l in self._pump(0.1)]):
                    self.taps.clear()
                    self.reset_seen = False
                    return
        raise IOError("TFT coprocessor not responding on " + PORT)

    def cmd(self, s):
        self.ser.write((s + "\n").encode("ascii", "replace"))
        end = time.monotonic() + 3
        got = False
        while not got and time.monotonic() < end:
            for l in self._pump(0.2):
                if self._dispatch(l):
                    got = True
        if not got:
            raise IOError("TFT no ACK: " + s[:30])

    def clear(self, c=BLACK):
        self.cmd(f"C {c}")

    def fill(self, x, y, w, h, c):
        self.cmd(f"R {x} {y} {w} {h} {c}")

    def rect(self, x, y, w, h, c):
        self.cmd(f"B {x} {y} {w} {h} {c}")

    def text(self, x, y, s, size=2, fg=WHITE, bg=-1):
        s = ascii_only(s)[:50]
        if s:
            self.cmd(f"T {x} {y} {size} {fg} {bg} {s}")

    def clear_taps(self):
        for l in self._pump(0):
            self._dispatch(l)
        self.taps.clear()

    def get_tap(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        while not self.taps:
            if self.reset_seen:
                return None
            if end is not None and time.monotonic() >= end:
                return None
            for l in self._pump(0.1):
                self._dispatch(l)
        return self.taps.pop(0)[:2]

    def get_raw_tap(self):
        """校正用: (rx, ry) を返す。旧ファームなら 'OLD'、328P 再起動なら None"""
        while not self.taps:
            if self.reset_seen:
                return None
            for l in self._pump(0.1):
                self._dispatch(l)
        raw = self.taps.pop(0)[2]
        return raw if raw is not None else "OLD"


class Screen:
    """ボタン(当たり判定)付きの1画面"""

    def __init__(self, d):
        self.d = d
        self.hits = []

    def begin(self, title, bg=BLACK):
        self.hits = []
        self.d.clear_taps()
        self.d.clear(bg)
        self.d.fill(0, 0, W, 40, NAVY)
        self.d.text(8, 12, title, 2, WHITE, NAVY)

    def button(self, x, y, w, h, label, key, fg=WHITE, bg=BLUE, size=2):
        self.d.fill(x + 2, y + 2, w - 4, h - 4, bg)
        tw, th = len(label) * 6 * size, 8 * size
        self.d.text(x + (w - tw) // 2, y + (h - th) // 2, label, size, fg, bg)
        self.hits.append((x, y, w, h, key))

    # 待機中に毎回呼ぶ関数(着信の割り込み用)。True を返したら画面を描き直させる
    idle_hook = None

    def wait(self, timeout=None):
        """押されたボタンの key を返す。328P 再起動時と着信画面から戻ったときは 'RESET'、
        タイムアウトは None"""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if Screen.idle_hook and Screen.idle_hook(self):
                return "RESET"
            left = 0.3 if end is None else min(0.3, max(0.0, end - time.monotonic()))
            t = self.d.get_tap(left)
            if t is None:
                if self.d.reset_seen:
                    self.d.after_reset()
                    return "RESET"
                if end is None or time.monotonic() < end:
                    continue
                return None
            for x, y, w, h, key in self.hits:
                if x <= t[0] < x + w and y <= t[1] < y + h:
                    return key


def wrap(text, width):
    out = []
    for para in str(text).splitlines():
        para = ascii_only(para.replace("\t", " "))
        while len(para) > width:
            out.append(para[:width])
            para = para[width:]
        out.append(para)
    return out


# ------------------------------------------------------------- Wi-Fi ----
def sh(args, timeout=60):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError as e:
        return -1, "", str(e)


def split_terse(line):
    """nmcli -t 出力を分割 (':' と '\\' はバックスラッシュでエスケープされる)"""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def is_open(net):
    return net["security"] in ("", "--")


def parse_scan(out):
    nets = {}
    for line in out.splitlines():
        f = split_terse(line)
        if len(f) < 6:
            continue
        inuse, ssid, sig, sec, chan, bssid = f[:6]
        if not ssid:
            continue  # ステルスSSIDは除外
        sig = int(sig) if sig.strip().isdigit() else 0
        used = inuse.strip() == "*"
        cur = nets.get(ssid)
        if cur is None or sig > cur["signal"]:
            nets[ssid] = dict(ssid=ssid, signal=sig, security=sec.strip(),
                              chan=chan.strip(), bssid=bssid,
                              inuse=used or bool(cur and cur["inuse"]))
        elif used:
            cur["inuse"] = True
    return sorted(nets.values(), key=lambda n: (not n["inuse"], -n["signal"]))


def scan():
    rc, out, err = sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN,BSSID",
                       "device", "wifi", "list", "--rescan", "yes", "ifname", IFACE], 45)
    return parse_scan(out), (rc, err.strip())


def connect(net, password=None):
    """専用プロファイル sip-<SSID> を作って接続する。
    ・WPA2/WPA3 混在APでは Pi Zero W の Wi-Fi チップが WPA3(SAE) に失敗するため
      WPA2 を含む場合は wpa-psk を明示する。
    ・キャプティブポータル対策で MAC アドレスはランダム化しない(permanent)。"""
    name = "sip-" + net["ssid"]
    sh(["nmcli", "connection", "delete", "id", name], 15)  # 前回分を作り直す
    sec = net.get("security", "")
    args = ["nmcli", "connection", "add", "type", "wifi", "ifname", IFACE,
            "con-name", name, "ssid", net["ssid"],
            "wifi.cloned-mac-address", "permanent"]
    if password:
        key = "sae" if ("WPA3" in sec and "WPA2" not in sec and "WPA1" not in sec) else "wpa-psk"
        args += ["wifi-sec.key-mgmt", key, "wifi-sec.psk", password]
    rc, out, err = sh(args, 20)
    msg = (out + err).strip()
    if rc == 0:
        rc, out, err = sh(["nmcli", "-w", "45", "connection", "up", "id", name], 60)
        msg = (out + err).strip()
        if rc != 0:
            sh(["nmcli", "connection", "delete", "id", name], 15)
    log_connect(net, password, rc, msg)
    return rc == 0, msg


def log_connect(net, password, rc, msg):
    """接続試行の記録(パスワード本体は残さず長さのみ)"""
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with open(os.path.join(LOGDIR, "connect.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} ssid={net['ssid']!r} "
                    f"sec={net.get('security')!r} pwlen={len(password or '')} rc={rc} "
                    f"msg={msg!r}\n")
    except OSError:
        pass


def ip_info():
    rc, out, _ = sh(["nmcli", "-t", "-f",
                     "GENERAL.CONNECTION,GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS",
                     "device", "show", IFACE], 10)
    info = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        k = k.split("[")[0]
        info.setdefault(k, []).append(v.replace("\\:", ":"))
    return {k: ", ".join(x for x in v if x) for k, v in info.items()}


# ---------------------------------------------------- キャプティブ対応 ----
# 判定/追跡/レシピは pc_tools と共通の captive_lib.py (同じフォルダに置く)
import threading

import captive_lib as cl

try:
    import phone as ph   # baresip 操作 (同じフォルダの phone.py)
except ImportError:
    ph = None

KEEP_INTERVAL = int(os.environ.get("KEEP_INTERVAL", "60"))  # 定期確認の間隔(秒)
_net_lock = threading.Lock()


def _log_portal(res):
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with open(os.path.join(LOGDIR, f"portal-{datetime.date.today():%Y%m%d}.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def ensure_online(auto_auth=True):
    """接続確認し、既知のポータルなら自動認証する。結果 dict を返す"""
    with _net_lock:
        res = {"time": datetime.datetime.now().strftime("%H:%M:%S"), "state": "NO_NET",
               "recipe": None, "detail": "", "authed": False}
        sess = cl.Session()
        pr = cl.probe(sess, "google")
        res["state"] = pr["state"]
        if pr["state"] == "NO_NET":
            res["detail"] = pr.get("error", "")
            return res
        if pr["state"] == "ONLINE":
            return res
        hops = cl.follow(sess, cl.PROBE_URLS["google"][0])
        last = hops[-1] if hops else {}
        res["portal_url"] = last.get("url")
        res["hops"] = [cl.hop_summary(h) for h in hops]
        name, recipe = cl.find_recipe(hops)
        res["recipe"] = name
        if not recipe:
            forms = cl.parse_forms(last.get("body"), last.get("url", ""))
            res["forms"] = forms
            res["detail"] = f"unknown portal, forms={len(forms)}"
            _log_portal(res)
            return res
        if not auto_auth:
            return res
        _, ok, rh = recipe(sess, hops)
        res["authed"] = True
        res["auth_resp"] = (rh[-1].get("body") or "")[:120] if rh else ""
        state = "PORTAL"
        for _ in range(5):  # 反映に数秒かかる
            time.sleep(1.5)
            state = cl.probe(cl.Session(), "google")["state"]
            if state == "ONLINE":
                break
        res["state"] = state
        res["detail"] = f"auth {name}: {'OK' if ok else 'NG'}"
        _log_portal(res)
        return res


def try_generic_form(res, form_index=0):
    """レシピの無いポータルで、フォーム送信(同意型)を1回試す"""
    with _net_lock:
        sess = cl.Session()
        hops = cl.follow(sess, cl.PROBE_URLS["google"][0])
        last = hops[-1] if hops else {}
        forms = cl.parse_forms(last.get("body"), last.get("url", ""))
        if form_index >= len(forms):
            return "no form"
        data, missing = cl.build_submission(forms[form_index])
        if missing:
            return "needs input: " + ",".join(missing)
        cl.submit(sess, forms[form_index], data)
    time.sleep(2)
    return "sent"


class Keeper(threading.Thread):
    """一定間隔で ensure_online を実行する(画面には触れない)。status を UI が読む"""

    def __init__(self):
        super().__init__(daemon=True)
        self.status = {"state": "-", "time": "", "detail": ""}
        self.seq = 0
        self.enabled = True
        self._wake = threading.Event()

    def check_now(self):
        self._wake.set()

    def run_once(self):
        try:
            st = ensure_online()
        except Exception as e:  # ネットワーク処理の例外で UI を落とさない
            st = {"state": "NO_NET", "time": datetime.datetime.now().strftime("%H:%M:%S"),
                  "detail": repr(e)[:100]}
        # 切断/ポータル状態から復帰したら SIP 登録をやり直す(着信できない時間を短くする)
        if st.get("state") == "ONLINE" and self.status.get("state") != "ONLINE" and PHONE:
            PHONE.reregister()
        self.status = st
        self.seq += 1
        return st

    def run(self):
        while True:
            self._wake.wait(KEEP_INTERVAL)
            self._wake.clear()
            if self.enabled:
                self.run_once()


KEEPER = Keeper()
PHONE = None   # phone.Baresip (main で起動。phone.py が無ければ None のまま)


# ---------------------------------------------------------------- 画面 ----
ROWS, ROW_H, LIST_Y = 8, 48, 44
FOOT_Y = H - 48


def bars(d, x, y, sig, bg):
    for i, th in enumerate((25, 45, 65, 80)):
        h = 8 + i * 6
        d.fill(x + i * 9, y + 26 - h, 6, h, GREEN if sig >= th else DGRAY if bg == BLACK else GRAY)


def draw_list(scr, nets, page, pages, err=""):
    d = scr.d
    scr.begin("Wi-Fi")
    cur = next((n["ssid"] for n in nets if n["inuse"]), None)
    d.text(76, 16, ("Now: " + cur)[:15] if cur else "Now: -", 1, CYAN, NAVY)
    scr.button(W - 80, 2, 80, 36, "BACK", "BACK", WHITE, GRAY, 1)
    if not nets:
        d.text(8, 60, "No networks found", 2, YELLOW)
        for i, l in enumerate(wrap(err, 52)[:10]):
            d.text(8, 90 + i * 12, l, 1, GRAY)
    for i, n in enumerate(nets[page * ROWS:(page + 1) * ROWS]):
        y = LIST_Y + i * ROW_H
        bg = DGRAY if i % 2 else BLACK
        if bg != BLACK:
            d.fill(0, y, W, ROW_H, bg)
        d.text(8, y + 6, n["ssid"][:22], 2, GREEN if n["inuse"] else WHITE, bg)
        sec = "OPEN" if is_open(n) else n["security"]
        d.text(8, y + 30, f"{n['signal']:3d}%  ch{n['chan']}  {sec}"[:44], 1,
               YELLOW if is_open(n) else GRAY, bg)
        bars(d, W - 42, y + 10, n["signal"], bg)
        scr.hits.append((0, y, W, ROW_H, ("NET", n)))
    scr.button(0, FOOT_Y, 80, 48, "<", "PREV")
    scr.button(80, FOOT_Y, 160, 48, f"SCAN {page + 1}/{pages}", "SCAN")
    scr.button(240, FOOT_Y, 80, 48, ">", "NEXT")


# ---- 携帯電話式(マルチタップ)キーボード ----
# パネル左端の約30pxはタッチ精度が悪いので使わない。キーは 96x62px と大きくとる。
KB_LM = 30                       # 左マージン
KB_COLS_W = (W - KB_LM) // 3     # 96
KB_Y, KB_H = 150, 62
MULTITAP_SEC = 1.2
KB_KEYS = [  # (表示, 文字の順番)
    ("1", ".,-_@!?/:1"), ("2", "abc2"), ("3", "def3"),
    ("4", "ghi4"), ("5", "jkl5"), ("6", "mno6"),
    ("7", "pqrs7"), ("8", "tuv8"), ("9", "wxyz9"),
    ("*", "'\"()[]{}<>^`|~\\"), ("0", " 0"), ("#", "#$%&*+=;"),
]


def keyboard(scr, ssid):
    """パスワード入力(マルチタップ)。OK で文字列、CANCEL で None"""
    d = scr.d
    pw = ""
    upper = False
    num = False          # 数字モード: 1回押しでキーの数字(* # も)を入力
    pend = None          # (キー番号, 文字インデックス, 押した時刻)

    def chars(i):
        if num:
            return KB_KEYS[i][0]
        cs = KB_KEYS[i][1]
        return cs.upper() if upper else cs

    def draw_pw():
        d.fill(KB_LM, 88, W - KB_LM - 4, 40, BLACK)
        d.rect(KB_LM, 88, W - KB_LM - 4, 40, GRAY)
        shown = pw[-19:]
        if pend and shown:
            d.text(KB_LM + 6, 100, shown[:-1], 2, WHITE, BLACK)
            d.text(KB_LM + 6 + (len(shown) - 1) * 12, 100, shown[-1], 2, BLACK, YELLOW)
            d.text(KB_LM + 6 + len(shown) * 12, 100, "_", 2, WHITE, BLACK)
        else:
            d.text(KB_LM + 6, 100, shown + "_", 2, WHITE, BLACK)
        d.fill(KB_LM, 131, W - KB_LM, 12, BLACK)
        mode = "123" if num else ("ABC" if upper else "abc")
        hint = "1 tap = 1 digit" if num else "tap again = next char"
        d.text(KB_LM, 132, f"{len(pw)} ch  [{mode}]  {hint}", 1, GRAY, BLACK)

    def draw_key(i):
        r, c = divmod(i, 3)
        x, y = KB_LM + c * KB_COLS_W, KB_Y + r * KB_H
        d.fill(x + 2, y + 2, KB_COLS_W - 4, KB_H - 4, DGRAY)
        label, cs = KB_KEYS[i]
        d.text(x + 10, y + 8, label, 3, WHITE, DGRAY)
        if not num:
            sub = (cs.upper() if upper else cs).replace(" ", "_")[:7]
            d.text(x + 10, y + 38, sub, 2 if len(sub) <= 6 else 1, CYAN, DGRAY)
        scr.hits.append((x, y, KB_COLS_W, KB_H, ("KEY", i)))

    def draw_all():
        scr.begin("Password")
        scr.button(W - 96, 2, 96, 36, "CANCEL", "CANCEL", WHITE, RED, 1)
        d.text(KB_LM, 50, "SSID: " + ssid[:20], 2, CYAN)
        draw_pw()
        for i in range(12):
            draw_key(i)
        cy = KB_Y + KB_H * 4 + 4
        bw = (W - KB_LM) // 4
        for i, (lab, key) in enumerate((("Aa", "SHIFT"), ("123", "NUM"), ("DEL", "DEL"), ("OK", "OK"))):
            scr.button(KB_LM + i * bw, cy, bw, H - cy, lab, key,
                       WHITE, GREEN if key == "OK" else BLUE)

    def redraw_keys():
        scr.hits = [h for h in scr.hits if not (isinstance(h[4], tuple) and h[4][0] == "KEY")]
        for i in range(12):
            draw_key(i)

    draw_all()
    while True:
        k = scr.wait(MULTITAP_SEC if pend else None)
        now = time.monotonic()
        if k is None:                      # マルチタップ確定(ハイライト解除)
            pend = None
            draw_pw()
            continue
        if k == "RESET":
            pend = None
            draw_all()
        elif k == "CANCEL":
            return None
        elif k == "OK":
            return pw
        elif k == "DEL":
            pw, pend = pw[:-1], None
            draw_pw()
        elif k == "SHIFT":
            upper, num, pend = not upper, False, None
            redraw_keys()
            draw_pw()
        elif k == "NUM":
            num, pend = not num, None
            redraw_keys()
            draw_pw()
        elif isinstance(k, tuple) and k[0] == "KEY":
            i = k[1]
            if num:
                if len(pw) < 63:
                    pw += chars(i)
                pend = None
            elif pend and pend[0] == i and now - pend[2] < MULTITAP_SEC:
                j = (pend[1] + 1) % len(chars(i))
                pw = pw[:-1] + chars(i)[j]
                pend = (i, j, now)
            elif len(pw) < 63:
                pw += chars(i)[0]
                pend = (i, 0, now)
            draw_pw()


def message(scr, title, lines, buttons, colors=None):
    """行を表示して下部ボタンを待つ"""
    d = scr.d
    while True:
        scr.begin(title)
        y = 50
        for i, (txt, size, color) in enumerate(lines):
            for l in wrap(txt, 52 if size == 1 else 26):
                if y > FOOT_Y - 8 * size:
                    break
                d.text(8, y, l, size, color)
                y += 10 * size + 2
        bw = W // len(buttons)
        for i, (lab, key) in enumerate(buttons):
            scr.button(i * bw, FOOT_Y, bw, 48, lab, key,
                       WHITE, (colors or {}).get(key, BLUE))
        k = scr.wait()
        if k != "RESET":
            return k


def connect_flow(scr, net, pw):
    d = scr.d
    scr.begin("Connect")
    d.text(8, 60, net["ssid"][:26], 2, CYAN)
    d.text(8, 100, "Connecting...", 2, YELLOW)
    ok, msg = connect(net, pw)
    if not ok:
        message(scr, "Connect", [(net["ssid"], 2, CYAN), ("FAILED", 2, RED),
                                 (f"security: {net.get('security') or 'OPEN'}  pw: {len(pw or '')} ch", 1, WHITE),
                                 (msg, 1, GRAY)],
                [("BACK", "BACK")])
        return
    KEEPER.enabled = True
    scr.begin("Connect")
    d.text(8, 60, net["ssid"][:26], 2, CYAN)
    d.text(8, 100, "Checking internet...", 2, YELLOW)
    d.text(8, 130, "(auto login if known portal)", 1, GRAY)
    st = KEEPER.run_once()
    status_screen(scr, net["ssid"], st)


def status_lines(ssid, st):
    info = ip_info()
    state = st.get("state", "-")
    color = {"ONLINE": GREEN, "PORTAL": YELLOW}.get(state, RED)
    lines = [(ssid, 2, CYAN),
             ("IP  " + info.get("IP4.ADDRESS", "-"), 1, WHITE),
             ("GW  " + info.get("IP4.GATEWAY", "-"), 1, WHITE),
             ("MAC " + info.get("GENERAL.HWADDR", "-"), 1, WHITE),
             (state, 3, color),
             (f"checked {st.get('time', '')}  (every {KEEP_INTERVAL}s)", 1, GRAY)]
    if st.get("recipe"):
        lines.append((f"portal: {st['recipe']}", 1, YELLOW))
    if st.get("detail"):
        lines.append((st["detail"], 1, WHITE))
    if st.get("auth_resp"):
        lines.append(("resp " + st["auth_resp"], 1, GRAY))
    if state == "PORTAL" and not st.get("recipe"):
        lines.append(("portal: " + str(st.get("portal_url")), 1, YELLOW))
        for fm in (st.get("forms") or [])[:2]:
            names = ",".join(f.get("name") or f.get("type") or f["tag"] for f in fm["fields"])
            lines.append((f"{fm['method']} {fm['action']} [{names}]", 1, GRAY))
        lines.append(("Unknown portal. TRY = send form once", 1, WHITE))
    return lines


def status_screen(scr, ssid, st):
    """接続状態を表示。Keeper の結果が変わったら描き直す"""
    d = scr.d
    seq = KEEPER.seq
    while True:
        st = KEEPER.status if KEEPER.seq else st
        buttons = [("RECHECK", "RECHECK"), ("LIST", "LIST"), ("DISC", "DISC")]
        if st.get("state") == "PORTAL" and not st.get("recipe") and st.get("forms"):
            buttons = buttons[:2] + [("TRY", "TRY")] + buttons[2:]  # 左端はタッチ精度が悪いので避ける
        scr.begin("Connect")
        y = 50
        for txt, size, color in status_lines(ssid, st):
            for l in wrap(txt, 52 if size == 1 else 26):
                if y > FOOT_Y - 8 * size:
                    break
                d.text(8, y, l, size, color)
                y += 10 * size + 2
        bw = W // len(buttons)
        for i, (lab, key) in enumerate(buttons):
            scr.button(i * bw, FOOT_Y, bw, 48, lab, key, WHITE, RED if key == "DISC" else BLUE)
        while True:
            k = scr.wait(timeout=3)
            if k is None:
                if KEEPER.seq != seq:      # 定期確認の結果が更新された
                    seq = KEEPER.seq
                    break
                continue
            break
        if k in (None, "RESET"):
            continue
        if k == "RECHECK":
            d.fill(0, FOOT_Y - 14, W, 12, BLACK)
            d.text(8, FOOT_Y - 14, "checking...", 1, YELLOW, BLACK)
            st = KEEPER.run_once()
            seq = KEEPER.seq
        elif k == "TRY":
            d.fill(0, FOOT_Y - 14, W, 12, BLACK)
            d.text(8, FOOT_Y - 14, "sending form...", 1, YELLOW, BLACK)
            r = try_generic_form(st)
            st = KEEPER.run_once()
            st["detail"] = f"form: {r}"
            seq = KEEPER.seq
        elif k == "DISC":
            sh(["nmcli", "device", "disconnect", IFACE], 20)
            return
        elif k == "LIST":
            return


# ------------------------------------------------------- タッチ校正 ----
CAL_COLS = (30, W // 2, W - 30)
CAL_ROWS = (30, H // 2, H - 30)


def save_cal(cal):
    with open(CAL_FILE, "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=1)


def calibrate(scr):
    """3x3=9点をタップして軸ごとの折れ線補正を作り、テスト後に保存する"""
    d = scr.d
    targets = [(tx, ty) for ty in CAL_ROWS for tx in CAL_COLS]
    while True:
        raws = []
        for i, (tx, ty) in enumerate(targets):
            scr.begin("Touch CAL")
            ly = H // 2 + 70 if ty <= H // 2 else H // 2 - 90
            d.text(8, ly, f"Tap center of + ({i + 1}/9)", 2, YELLOW)
            d.text(8, ly + 24, "Use a stylus. Tap once.", 1, GRAY)
            d.cmd(f"L {tx - 15} {ty} {tx + 15} {ty} {WHITE}")
            d.cmd(f"L {tx} {ty - 15} {tx} {ty + 15} {WHITE}")
            d.rect(tx - 3, ty - 3, 7, 7, RED)
            r = d.get_raw_tap()
            if r is None:
                d.after_reset()
                break
            if r == "OLD":
                message(scr, "Touch CAL", [("328P firmware is old.", 2, RED),
                                           ("Re-flash tft_coproc.ino (M command).", 1, GRAY)],
                        [("OK", "OK")])
                return
            raws.append(r)
            d.fill(tx - 16, ty - 16, 33, 33, GREEN)
            time.sleep(0.3)
        if len(raws) < len(targets):
            continue
        # 9点の実測値をそのまま使うメッシュ補正
        new = {"pts": [[r[0], r[1], tx, ty] for r, (tx, ty) in zip(raws, targets)]}
        raw_lines = [" ".join(f"{r[0]},{r[1]}" for r in raws[i:i + 3]) for i in (0, 3, 6)]
        if not mesh_valid(new["pts"]):
            if message(scr, "Touch CAL", [("FAILED", 2, RED),
                                          ("Points not in order. Tap the + centers.", 1, GRAY)]
                       + [(l, 1, GRAY) for l in raw_lines],
                       [("RETRY", "RETRY"), ("CANCEL", "CANCEL")]) == "RETRY":
                continue
            return
        # テスト: 新しい補正で押した位置に点を打つ
        scr.begin("Touch CAL test")
        d.text(8, 50, "Tap anywhere. A dot shows", 1, WHITE)
        d.text(8, 62, "where the touch lands.", 1, WHITE)
        d.text(8, 76, "raw x,y (row by row):", 1, CYAN)
        for i, l in enumerate(raw_lines):
            d.text(8, 88 + i * 12, l, 1, CYAN)
        for tx, ty in targets:
            d.cmd(f"L {tx - 10} {ty} {tx + 10} {ty} {GRAY}")
            d.cmd(f"L {tx} {ty - 10} {tx} {ty + 10} {GRAY}")
        bw = W // 3
        for i, (lab, key) in enumerate((("SAVE", "SAVE"), ("RETRY", "RETRY"), ("CANCEL", "CANCEL"))):
            scr.button(i * bw, FOOT_Y, bw, 48, lab, key, WHITE, GREEN if key == "SAVE" else BLUE)
        while True:
            r = d.get_raw_tap()
            if r is None or r == "OLD":
                d.after_reset()
                key = "RETRY"
                break
            x, y = d.to_screen(r[0], r[1], new)
            key = next((k for bx, by, w, h, k in scr.hits
                        if bx <= x < bx + w and by <= y < by + h), None)
            if key:
                break
            d.fill(x - 2, y - 2, 5, 5, RED)
            d.text(8, FOOT_Y - 14, f"last raw {r[0]},{r[1]} -> {x},{y}   ", 1, YELLOW, BLACK)
        if key == "SAVE":
            d.cal = new
            try:
                save_cal(new)
            except OSError as e:
                message(scr, "Touch CAL", [("Save failed", 2, RED), (str(e), 1, GRAY)], [("OK", "OK")])
            return
        if key == "CANCEL":
            return


def headless():
    """TFT(328P)が応答しないときの予備動作: 画面なしで定期確認と自動認証だけ続ける。
    保存済みプロファイル(sip-<SSID>)へは NetworkManager が自動接続する。"""
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, "headless.log")
    last = None
    while True:
        st = KEEPER.run_once()
        line = (f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {ip_info().get('GENERAL.CONNECTION', '-')} "
                f"{st.get('state')} {st.get('recipe') or ''} {st.get('detail', '')}")
        if line[20:] != last:  # 状態が変わったときだけ記録
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
            last = line[20:]
        print(line, flush=True)
        time.sleep(KEEP_INTERVAL)


def other_instance():
    """自分以外の wifi_ui.py が動いていれば その PID を返す (シリアルの取り合い防止)"""
    r = subprocess.run(["pgrep", "-f", "wifi_ui.py"], capture_output=True, text=True)
    mine, pid = set(), os.getpid()      # 自分と祖先(sudo など)を除外
    while pid > 1 and pid not in mine:
        mine.add(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    pids = [p for p in r.stdout.split() if p.isdigit() and int(p) not in mine]
    return pids




# ------------------------------------------------------------- 電話 ----
IN_CALL = False      # 通話画面を表示中(着信割り込みの二重起動防止)
_cache = {}


def cached(key, sec, fn):
    """fn() の結果を sec 秒間使い回す(nmcli/tailscale の呼び出しは Zero W では重い)"""
    v = _cache.get(key)
    if v is None or time.monotonic() - v[0] > sec:
        v = (time.monotonic(), fn())
        _cache[key] = v
    return v[1]


def tailscale_ip():
    rc, out, _ = sh(["tailscale", "ip", "-4"], 5)
    return out.strip().splitlines()[0] if rc == 0 and out.strip() else "-"


def wifi_name():
    n = ip_info().get("GENERAL.CONNECTION", "")
    return "" if n in ("", "--") else n


def fmt_dur(sec):
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def peer_name(ext):
    return (ph.contact_name(ext) if ph else "") or ""


def phone_hook(scr):
    """Screen.wait から呼ばれる。通話中(着信・ボタン応答など)なら通話画面を出す"""
    if IN_CALL or not PHONE or not PHONE.call:
        return False
    if PHONE.call["state"] == "CLOSED":
        PHONE.clear_call()       # 画面を出す前に終わった通話(不在着信など)
        return False
    call_screen(scr)
    return True


def start_call(scr, ext):
    if not PHONE or not PHONE.connected:
        message(scr, "Call", [("baresip is not running", 2, RED),
                              ("sudo systemctl status baresip", 1, GRAY)], [("OK", "OK")])
        return
    PHONE.dial(ext)
    if PHONE.call:           # 失敗時も理由を「Call ended」画面で表示する
        call_screen(scr)


CALL_TITLE = {"INCOMING": ("Incoming call", YELLOW), "OUTGOING": ("Calling...", CYAN),
              "RINGING": ("Ringing...", CYAN), "ESTABLISHED": ("In call", GREEN),
              "CLOSED": ("Call ended", GRAY)}


def call_screen(scr):
    global IN_CALL
    IN_CALL = True
    try:
        _call_screen(scr)
    finally:
        IN_CALL = False


def _call_screen(scr):
    d = scr.d
    drawn, vol = None, ""
    while True:
        c = PHONE.call
        if not c:
            return
        st = c["state"]
        if (st, PHONE.muted) != drawn:
            drawn = (st, PHONE.muted)
            scr.begin("Call")
            t, col = CALL_TITLE.get(st, (st, WHITE))
            d.text(KB_LM, 56, t, 3, col)
            name = peer_name(c["peer"])
            d.text(KB_LM, 110, (name or c["peer"])[:11], 4, WHITE)
            if name:
                d.text(KB_LM, 152, c["peer"][:24], 2, CYAN)
            if st == "CLOSED":
                d.text(KB_LM, 200, fmt_dur(c.get("dur", 0)), 3, GRAY)
                for i, l in enumerate(wrap(c.get("reason", ""), 48)[:3]):
                    d.text(KB_LM, 240 + i * 12, l, 1, GRAY)
            if PHONE.muted and st != "CLOSED":
                d.text(KB_LM, 240, "MIC MUTED", 2, RED)
            if st == "INCOMING":
                scr.button(KB_LM, 360, 140, 110, "REJECT", "HANGUP", WHITE, RED)
                scr.button(KB_LM + 150, 360, W - KB_LM - 150, 110, "ANSWER", "ANSWER", BLACK, GREEN)
            elif st in ("OUTGOING", "RINGING"):
                scr.button(KB_LM, 360, W - KB_LM, 110, "HANGUP", "HANGUP", WHITE, RED, 3)
            elif st == "ESTABLISHED":
                for i, (lab, key) in enumerate((("VOL-", "VOL-"), ("VOL+", "VOL+"),
                                                ("UNMUTE" if PHONE.muted else "MUTE", "MUTE"))):
                    scr.button(KB_LM + i * KB_COLS_W, 280, KB_COLS_W, 70, lab, key)
                scr.button(KB_LM, 360, W - KB_LM, 110, "HANGUP", "HANGUP", WHITE, RED, 3)
            if vol:
                d.text(KB_LM, 264, f"volume {vol}  ", 1, WHITE, BLACK)
        if st == "CLOSED":
            scr.wait(2.0)            # 終了表示(タップで早送り)
            PHONE.clear_call()
            return
        if st == "ESTABLISHED" and c.get("t0"):
            d.text(KB_LM, 200, fmt_dur(time.monotonic() - c["t0"]), 3, WHITE, BLACK)
        k = scr.wait(1.0)
        if k == "RESET":
            drawn = None
        elif k == "ANSWER":
            PHONE.accept()
        elif k == "HANGUP":
            PHONE.hangup()
        elif k == "MUTE":
            PHONE.toggle_mute()
        elif k in ("VOL+", "VOL-"):
            vol = ph.volume(5 if k == "VOL+" else -5)
            d.text(KB_LM, 264, f"volume {vol}  ", 1, WHITE, BLACK)


DP_Y, DP_H = 100, 66


def dialpad(scr):
    d = scr.d
    num = ""

    def draw_num():
        d.fill(KB_LM, 48, W - KB_LM - 4, 44, BLACK)
        d.rect(KB_LM, 48, W - KB_LM - 4, 44, GRAY)
        d.text(KB_LM + 8, 58, num[-15:] + "_", 3, WHITE, BLACK)

    def draw_all():
        scr.begin("Dial")
        scr.button(W - 80, 2, 80, 36, "BACK", "BACK", WHITE, GRAY, 1)
        draw_num()
        for i in range(12):
            r, c = divmod(i, 3)
            lab = KB_KEYS[i][0]
            scr.button(KB_LM + c * KB_COLS_W, DP_Y + r * DP_H, KB_COLS_W, DP_H, lab, ("KEY", lab),
                       WHITE, DGRAY, 3)
        y = DP_Y + 4 * DP_H + 4
        scr.button(KB_LM, y, KB_COLS_W, H - y, "DEL", "DEL")
        scr.button(KB_LM + KB_COLS_W, y, W - KB_LM - KB_COLS_W, H - y, "CALL", "CALL", BLACK, GREEN, 3)

    draw_all()
    while True:
        k = scr.wait()
        if k == "RESET":
            draw_all()
        elif k == "BACK":
            return
        elif k == "DEL":
            num = num[:-1]
            draw_num()
        elif k == "CALL":
            if num:
                start_call(scr, num)
                return
        elif isinstance(k, tuple) and k[0] == "KEY" and len(num) < 20:
            num += k[1]
            draw_num()


HIST_LABEL = {"in": ("IN", GREEN), "out": ("OUT", CYAN), "miss": ("MISS", RED)}


def history_screen(scr):
    d = scr.d
    while True:
        scr.begin("History")
        scr.button(W - 80, 2, 80, 36, "BACK", "BACK", WHITE, GRAY, 1)
        hist = ph.load_history()[:ROWS] if ph else []
        if not hist:
            d.text(8, 60, "No calls yet", 2, GRAY)
        for i, e in enumerate(hist):
            y = LIST_Y + i * ROW_H
            bg = DGRAY if i % 2 else BLACK
            if bg != BLACK:
                d.fill(0, y, W, ROW_H, bg)
            lab, col = HIST_LABEL.get(e.get("kind"), ("?", WHITE))
            ext = str(e.get("ext", ""))
            d.text(8, y + 6, lab, 2, col, bg)
            d.text(70, y + 6, (peer_name(ext) or ext)[:20], 2, WHITE, bg)
            dur = fmt_dur(e.get("dur", 0)) if e.get("kind") != "miss" else "--:--"
            d.text(70, y + 30, f"{e.get('t', '')}  {dur}  {ext}"[:40], 1, GRAY, bg)
            scr.hits.append((0, y, W, ROW_H, ("DIAL", ext)))
        k = scr.wait()
        if k == "BACK":
            return
        if isinstance(k, tuple) and k[0] == "DIAL":
            start_call(scr, k[1])
            return


def sip_status():
    if not PHONE:
        return "SIP: phone.py missing", RED
    if not PHONE.connected:
        return "SIP: baresip not running", RED
    return {"OK": ("SIP: registered", GREEN),
            "FAIL": ("SIP: register FAILED", RED)}.get(PHONE.reg, ("SIP: registering...", YELLOW))


def home_status():
    sip, col = sip_status()
    wifi = cached("wifi", 15, wifi_name) or "(not connected)"
    return (sip, col, f"Wi-Fi {wifi[:20]}  {KEEPER.status.get('state', '-')}",
            "Tailscale " + cached("ts", 30, tailscale_ip))


def draw_home_status(d, st):
    sip, col, wifi, ts = st
    d.fill(0, 44, W, 56, BLACK)
    d.text(8, 48, sip, 2, col, BLACK)
    d.text(8, 72, wifi, 1, WHITE, BLACK)
    d.text(8, 86, ts, 1, WHITE, BLACK)


def home(scr):
    d = scr.d
    while True:
        my = ph.uri_user(PHONE.aor) if PHONE and PHONE.aor else ""
        scr.begin("Phone " + my)
        scr.button(W - 56, 2, 56, 36, "OFF", "OFF", WHITE, RED, 1)
        st = home_status()
        draw_home_status(d, st)
        cw = (W - KB_LM) // 2       # 145
        for i, c in enumerate((ph.load_contacts() if ph else [])[:4]):
            r, col = divmod(i, 2)
            x, y = KB_LM + col * cw, 104 + r * 66
            d.fill(x + 2, y + 2, cw - 4, 62, BLUE)
            d.text(x + 10, y + 10, str(c.get("name", ""))[:11], 2, WHITE, BLUE)
            d.text(x + 10, y + 38, str(c["ext"])[:20], 2, CYAN, BLUE)
            scr.hits.append((x, y, cw, 66, ("DIAL", str(c["ext"]))))
        scr.button(KB_LM, 240, cw, 80, "DIAL", "DIAL", BLACK, GREEN, 3)
        scr.button(KB_LM + cw, 240, cw, 80, "HIST", "HIST", WHITE, BLUE, 3)
        for i, e in enumerate((ph.load_history() if ph else [])[:3]):
            lab, col = HIST_LABEL.get(e.get("kind"), ("?", WHITE))
            ext = str(e.get("ext", ""))
            d.text(8, 334 + i * 18, f"{lab:4s} {(peer_name(ext) or ext)[:12]:12s} {e.get('t', '')}", 1, col)
        scr.button(KB_LM, FOOT_Y, cw, 48, "WIFI", "WIFI")
        scr.button(KB_LM + cw, FOOT_Y, cw, 48, "CAL", "CAL", WHITE, GRAY)
        seq = PHONE.seq if PHONE else 0
        while True:
            k = scr.wait(3)
            if k is not None:
                break
            new = home_status()
            if new != st:
                st = new
                draw_home_status(d, st)
            if PHONE and PHONE.seq != seq and PHONE.aor and ph.uri_user(PHONE.aor) != my:
                k = "RESET"          # 自分の内線番号が分かったので見出しを描き直す
                break
        if k == "DIAL":
            dialpad(scr)
        elif k == "HIST":
            history_screen(scr)
        elif k == "WIFI":
            wifi_menu(scr)
            _cache.pop("wifi", None)
        elif k == "CAL":
            calibrate(scr)
        elif k == "OFF":
            if message(scr, "Power", [("Power off?", 2, WHITE)],
                       [("YES", "YES"), ("NO", "NO")], {"YES": RED}) == "YES":
                scr.begin("Power")
                d.text(8, 60, "Shutting down.", 2, WHITE)
                d.text(8, 90, "Unplug after 20s.", 2, YELLOW)
                sh(["systemctl", "poweroff"], 10)
                time.sleep(60)
        elif isinstance(k, tuple) and k[0] == "DIAL":
            start_call(scr, k[1])


def wifi_menu(scr):
    d = scr.d
    nets, page, need_scan, err = [], 0, True, ""
    while True:
        if need_scan:
            scr.begin("Wi-Fi")
            d.text(8, 60, "Scanning...", 2, YELLOW)
            nets, (rc, err) = scan()
            page, need_scan = 0, False
        pages = max(1, (len(nets) + ROWS - 1) // ROWS)
        page %= pages
        draw_list(scr, nets, page, pages, err)
        k = scr.wait()
        if k == "BACK":
            return
        if k == "PREV":
            page -= 1
        elif k == "NEXT":
            page += 1
        elif k == "SCAN":
            need_scan = True
        elif isinstance(k, tuple) and k[0] == "NET":
            net, pw = k[1], None
            if not is_open(net):
                pw = keyboard(scr, net["ssid"])
                if pw is None:
                    continue
            connect_flow(scr, net, pw)
            need_scan = True


def main():
    global PHONE
    others = other_instance()
    if others:
        print(f"wifi_ui.py が既に動いています (PID {' '.join(others)})。"
              "先に: sudo systemctl stop wifi-ui; sudo pkill -f wifi_ui.py", flush=True)
        sys.exit(1)
    if ph:
        PHONE = ph.start()   # 画面が無くてもヘッドセットのボタンで応答/切断できる
    try:
        d = Display(PORT)
    except Exception as e:
        print(f"TFT not available ({e}) -> headless mode", flush=True)
        headless()
        return
    scr = Screen(d)
    Screen.idle_hook = phone_hook
    KEEPER.start()  # 起動時から定期確認(保存済み Wi-Fi に自動接続した場合も再認証)
    if "--cal" in sys.argv:
        calibrate(scr)
    if not wifi_name():
        wifi_menu(scr)       # 未接続なら先に Wi-Fi を選ぶ
    home(scr)


if __name__ == "__main__":
    main()
