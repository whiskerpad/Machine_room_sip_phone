"""
phone.py - baresip(ctrl_tcp) の操作、ヘッドセットのボタン、短縮ダイヤル/通話履歴。
wifi_ui.py から import して使う(画面描画はしない)。

baresip 側の設定(baresip_setup.sh が行う):
  module_app ctrl_tcp.so / ctrl_tcp_listen 127.0.0.1:4444
通信: netstring ("<長さ>:<JSON>,") で JSON を送受信する。
  送信 {"command":"dial","params":"1001","token":"3"}
  受信 {"response":true,"ok":true,"data":"...","token":"3"}
       {"event":true,"class":"call","type":"CALL_INCOMING","peeruri":"sip:1001@...","id":"..."}
"""
import datetime
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CTRL_ADDR = ("127.0.0.1", int(os.environ.get("BARESIP_CTRL_PORT", "4444")))
AUDIO_CARD = os.environ.get("AUDIO_CARD", "ANGVEPC10A")          # aplay -l のカード名
HEADSET_NAME = os.environ.get("HEADSET_INPUT", "ANGV-EPC10-A")  # /proc/bus/input/devices の Name
CONTACTS_FILE = os.path.join(HERE, "contacts.json")
HISTORY_FILE = os.path.join(HERE, "call_log.json")
HISTORY_MAX = 30
DEFAULT_CONTACTS = [{"name": "PC", "ext": "1001"}, {"name": "iPhone", "ext": "1002"}]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def log(*a):
    print("[phone]", *a, flush=True)


# ------------------------------------------------------ 短縮ダイヤル/履歴 ----
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        log("save failed", path, e)


def load_contacts():
    c = _load_json(CONTACTS_FILE, None)
    if not isinstance(c, list):
        c = DEFAULT_CONTACTS
        _save_json(CONTACTS_FILE, c)   # 初回は雛形を作る(名前と番号を編集して使う)
    return [x for x in c if isinstance(x, dict) and str(x.get("ext", "")).strip()]


def contact_name(ext):
    for c in load_contacts():
        if str(c.get("ext")) == str(ext):
            return str(c.get("name") or ext)
    return ""


def load_history():
    h = _load_json(HISTORY_FILE, [])
    return h if isinstance(h, list) else []


def add_history(kind, ext, dur):
    """kind: in / out / miss。新しい順に保存"""
    h = load_history()
    h.insert(0, {"t": datetime.datetime.now().strftime("%m/%d %H:%M"),
                 "kind": kind, "ext": ext, "dur": int(dur)})
    _save_json(HISTORY_FILE, h[:HISTORY_MAX])


def uri_user(uri):
    """sip:1001@<SERVER_TS_IP>;transport=udp -> 1001"""
    m = re.search(r"(?:sips?:)?([^@:;>]+)@", uri or "")
    return m.group(1) if m else (uri or "?")


def volume(step):
    """再生音量を step(%) 増減。戻り値は現在値の文字列"""
    arg = f"{abs(step)}%{'+' if step > 0 else '-'}"
    try:
        r = subprocess.run(["amixer", "-c", AUDIO_CARD, "sset", "PCM", arg],
                           capture_output=True, text=True, timeout=5)
        m = re.search(r"\[(\d+)%\]", r.stdout)
        return m.group(1) + "%" if m else "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


# ---------------------------------------------------------- baresip ----
class Baresip(threading.Thread):
    """ctrl_tcp に常時接続し、登録状態と通話状態を保持する。
    UI は seq が変わったかどうかで再描画を判断する。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False
        self.reg = "?"           # OK / FAIL / ?
        self.reg_detail = ""
        self.aor = ""
        self.call = None         # dict: id dir peer state t0 reason
        self.muted = False
        self.seq = 0
        self._token = 0
        self._last_reginfo = 0.0

    # --- 送信 ---
    def send(self, command, params=""):
        with self.lock:
            if not self.sock:
                return False
            self._token += 1
            body = json.dumps({"command": command, "params": params,
                               "token": str(self._token)}).encode()
            try:
                self.sock.sendall(str(len(body)).encode() + b":" + body + b",")
                return True
            except OSError:
                return False

    def _changed(self):
        self.seq += 1

    # --- 操作(UI/ボタンから呼ぶ) ---
    def dial(self, ext):
        ext = str(ext).strip()
        if not ext:
            return False
        if self.call and self.call["state"] != "CLOSED":
            return False
        self.call = {"id": None, "dir": "out", "peer": ext, "state": "OUTGOING",
                     "t0": None, "reason": ""}
        self.muted = False
        self._changed()
        if not self.send("dial", ext):
            self._close("baresip not running")
            return False
        return True

    def accept(self):
        if self.call and self.call["state"] == "INCOMING":
            self.send("accept")

    def hangup(self):
        if self.call and self.call["state"] != "CLOSED":
            self.send("hangup")
            if self.call["id"] is None:      # 発信直後でまだイベントが来ていない
                self._close("cancelled")

    def toggle_mute(self):
        if self.call and self.call["state"] == "ESTABLISHED":
            if self.send("mute"):
                self.muted = not self.muted
                self._changed()

    def reregister(self):
        """Wi-Fi の再接続・再認証後に登録をやり直す"""
        self.send("uareg", "600")

    def clear_call(self):
        if self.call and self.call["state"] == "CLOSED":
            self.call = None
            self._changed()

    def button(self, action):
        """ヘッドセットのボタン: answer=応答/切断の兼用, mute, vol+, vol-"""
        c = self.call
        if action == "answer":
            if c and c["state"] == "INCOMING":
                self.accept()
            elif c and c["state"] in ("OUTGOING", "RINGING", "ESTABLISHED"):
                self.hangup()
        elif action == "mute":
            self.toggle_mute()
        elif action in ("vol+", "vol-"):
            volume(5 if action == "vol+" else -5)

    # --- 受信 ---
    def _close(self, reason):
        c = self.call
        if not c or c["state"] == "CLOSED":
            return
        dur = time.monotonic() - c["t0"] if c["t0"] else 0
        kind = c["dir"] if c["t0"] or c["dir"] == "out" else "miss"
        add_history(kind, c["peer"], dur)
        c["state"], c["reason"], c["dur"] = "CLOSED", reason, dur
        self._changed()

    def _on_event(self, ev):
        typ = ev.get("type", "")
        if ev.get("class") == "register" or typ.startswith("REGISTER"):
            if typ == "REGISTER_OK":
                self.reg, self.reg_detail = "OK", ev.get("param", "")
            elif typ == "REGISTER_FAIL":
                self.reg, self.reg_detail = "FAIL", ev.get("param", "")
            if ev.get("accountaor"):
                self.aor = ev["accountaor"]
            self._changed()
            return
        if ev.get("class") != "call" and not typ.startswith("CALL_"):
            return
        cid = ev.get("id")
        peer = uri_user(ev.get("peeruri") or ev.get("param", ""))
        c = self.call
        if typ == "CALL_INCOMING":
            if c and c["state"] != "CLOSED":
                log("second incoming call ignored:", peer)   # 1通話のみ対応
                return
            self.call = {"id": cid, "dir": "in", "peer": peer, "state": "INCOMING",
                         "t0": None, "reason": ""}
            self.muted = False
            self._changed()
            return
        if not c or (c["id"] and cid and c["id"] != cid):
            return                                  # 無関係の通話
        if c["id"] is None and cid:
            c["id"] = cid
        if typ in ("CALL_RINGING", "CALL_PROGRESS"):
            if c["state"] == "OUTGOING":
                c["state"] = "RINGING"
                self._changed()
        elif typ == "CALL_ESTABLISHED":
            c["state"], c["t0"] = "ESTABLISHED", time.monotonic()
            self._changed()
        elif typ == "CALL_CLOSED":
            self._close(ev.get("param", ""))

    def _on_response(self, r):
        data = _ANSI.sub("", str(r.get("data", "")))
        if "User Agents" in data or "sip:" in data and ("OK" in data or "ERR" in data):
            # reginfo の結果: "> sip:1003@<SERVER_TS_IP>  OK" のような行
            for line in data.splitlines():
                if "sip:" in line:
                    m = re.search(r"(sips?:[^\s>]+)", line)
                    if m:
                        self.aor = m.group(1)
                    st = "OK" if re.search(r"\bOK\b", line) else "FAIL" if "ERR" in line else None
                    if st and st != self.reg:
                        self.reg = st
                        self._changed()
                    break
        if not r.get("ok", True):
            log("command failed:", data[:120])
            if self.call and self.call["state"] == "OUTGOING" and self.call["id"] is None:
                self._close(data.strip()[:40] or "dial failed")

    def _on_message(self, msg):
        try:
            obj = json.loads(msg)
        except ValueError:
            return
        if obj.get("event"):
            self._on_event(obj)
        elif obj.get("response"):
            self._on_response(obj)

    def run(self):
        while True:
            try:
                s = socket.create_connection(CTRL_ADDR, timeout=3)
            except OSError:
                if self.connected or self.reg != "?":
                    self.connected, self.reg = False, "?"
                    self._changed()
                time.sleep(3)
                continue
            s.settimeout(1.0)
            with self.lock:
                self.sock = s
            self.connected = True
            self._changed()
            log("connected to baresip")
            self.send("reginfo")
            self._last_reginfo = time.monotonic()
            buf = b""
            try:
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    except socket.timeout:
                        pass
                    while True:              # netstring を取り出す
                        i = buf.find(b":")
                        if i < 0 or not buf[:i].isdigit():
                            if i > 10 or (i < 0 and len(buf) > 10):
                                buf = b""    # 同期ずれ: 捨てる
                            break
                        n = int(buf[:i])
                        if len(buf) < i + 1 + n + 1:
                            break
                        self._on_message(buf[i + 1:i + 1 + n].decode("utf-8", "replace"))
                        buf = buf[i + 2 + n:]
                    if time.monotonic() - self._last_reginfo > 30:
                        self._last_reginfo = time.monotonic()
                        self.send("reginfo")
            except OSError:
                pass
            with self.lock:
                self.sock = None
            try:
                s.close()
            except OSError:
                pass
            self.connected, self.reg = False, "?"
            if self.call and self.call["state"] != "CLOSED":
                self._close("baresip stopped")
            self._changed()
            log("baresip disconnected")
            time.sleep(2)


# ---------------------------------------------------- ヘッドセットのボタン ----
# struct input_event: timeval(long, long) + type(u16) + code(u16) + value(s32)
EV_FMT = "llHHi"
EV_SIZE = struct.calcsize(EV_FMT)
EV_KEY = 1
KEYMAP = {
    164: "answer",   # KEY_PLAYPAUSE
    200: "answer",   # KEY_PLAYCD
    201: "answer",   # KEY_PAUSECD
    226: "answer",   # KEY_MEDIA
    169: "answer",   # KEY_PHONE
    248: "mute",     # KEY_MICMUTE
    113: "mute",     # KEY_MUTE
    115: "vol+",     # KEY_VOLUMEUP
    114: "vol-",     # KEY_VOLUMEDOWN
}


def find_input(name):
    """/proc/bus/input/devices から Name に name を含む eventN を探す"""
    try:
        with open("/proc/bus/input/devices") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return None
    for b in blocks:
        if name in b:
            m = re.search(r"Handlers=.*?\b(event\d+)", b)
            if m:
                return "/dev/input/" + m.group(1)
    return None


class Headset(threading.Thread):
    """ヘッドセットのボタンを読み、Baresip.button() に渡す。抜き差しにも追従する"""

    def __init__(self, phone, name=HEADSET_NAME):
        super().__init__(daemon=True)
        self.phone = phone
        self.name = name
        self.last_key = None     # 画面の表示・調査用 (code, action)

    def run(self):
        while True:
            dev = find_input(self.name)
            if not dev:
                time.sleep(5)
                continue
            log("headset buttons:", dev)
            try:
                with open(dev, "rb") as f:
                    while True:
                        data = f.read(EV_SIZE)
                        if len(data) < EV_SIZE:
                            break
                        _, _, typ, code, value = struct.unpack(EV_FMT, data)
                        if typ != EV_KEY or value != 1:   # 押した瞬間だけ
                            continue
                        act = KEYMAP.get(code)
                        self.last_key = (code, act)
                        log(f"key code={code} -> {act}")
                        if act:
                            self.phone.button(act)
            except OSError as e:
                log("headset read:", e)
            time.sleep(2)


def start():
    """Baresip と Headset のスレッドを起動して Baresip を返す"""
    ph = Baresip()
    ph.start()
    hs = Headset(ph)
    hs.start()
    ph.headset = hs
    return ph


if __name__ == "__main__":
    # 単体テスト: python3 phone.py [番号]  → 状態変化とボタンを表示する(Ctrl+C で終了)
    import sys
    ph = start()
    seq = -1
    if len(sys.argv) > 1:
        time.sleep(1.5)
        ph.dial(sys.argv[1])
    while True:
        if ph.seq != seq:
            seq = ph.seq
            print(f"conn={ph.connected} reg={ph.reg} aor={ph.aor} call={ph.call}", flush=True)
        time.sleep(0.2)
