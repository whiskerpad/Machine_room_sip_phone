#!/usr/bin/env python3
"""
touch_check.py - 抵抗膜タッチパネルの健全性チェック (TFT + 328P tft_coproc 用)

  1) 無操作テスト : 触っていない5秒間に「押された」値が出ないか (膜の常時接触・短絡)
  2) 9点テスト    : 画面の + を順に押し、生値の範囲・順序・ゆがみ・ノイズ・不感帯を調べる
  3) 自由描画     : 9点の結果で仮に位置合わせして、なぞった所に点を打つ (目視確認, 20秒)

使い方 (wifi_ui を止めてから):
    sudo systemctl stop wifi-ui; sudo pkill -f wifi_ui.py
    python3 touch_check.py            # 全部
    python3 touch_check.py --no-draw  # 自由描画を省略
結果: 画面とターミナルに判定。詳細は ~/touch_check_<日時>.json
"""
import argparse
import datetime
import json
import os
import statistics as st
import subprocess
import sys
import threading
import time

import serial

PORT, BAUD = "/dev/serial0", 250000
W, H = 320, 480
BLACK, WHITE, RED, GREEN, YELLOW, CYAN, GRAY, NAVY = 0, 0xFFFF, 0xF800, 0x07E0, 0xFFE0, 0x07FF, 0x8410, 0x000F
ZMIN, ZMAX = 200, 5000          # 押下とみなす z の範囲 (tft_coproc の判定 z>200 と同じ下限)
COLS, ROWS = (30, 160, 290), (40, 240, 440)


class Link:
    """328P とのやり取り。Z 行(生値)は別スレッドで溜める"""

    def __init__(self, port):
        self.s = serial.Serial(port, BAUD, timeout=0.05)
        self.lock = threading.Lock()
        self.acks = 0
        self.z = []                 # (time, x, y, z)
        self.other = []
        self.alive = True
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        buf = b""
        while self.alive:
            buf += self.s.read(256)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                t = line.decode("ascii", "replace").strip()
                if t == "K":
                    self.acks += 1
                elif t.startswith("Z "):
                    try:
                        x, y, z = map(int, t[2:].split())
                        with self.lock:
                            self.z.append((time.monotonic(), x, y, z))
                    except ValueError:
                        pass
                elif t:
                    self.other.append(t)

    def cmd(self, c, timeout=3):
        n = self.acks
        self.s.write((c + "\n").encode("ascii", "replace"))
        end = time.monotonic() + timeout
        while self.acks == n:
            if time.monotonic() > end:
                raise IOError("no ACK for: " + c[:20])
            time.sleep(0.002)

    def take(self):
        with self.lock:
            z, self.z = self.z, []
        return z

    # 描画
    def clear(self, c=BLACK):
        self.cmd(f"C {c}")

    def text(self, x, y, s, size=2, fg=WHITE, bg=-1):
        self.cmd(f"T {x} {y} {size} {fg} {bg} {s[:48]}")

    def cross(self, x, y, c=WHITE):
        self.cmd(f"L {x - 15} {y} {x + 15} {y} {c}")
        self.cmd(f"L {x} {y - 15} {x} {y + 15} {c}")
        self.cmd(f"R {x - 2} {y - 2} 5 5 {RED}")

    def fill(self, x, y, w, h, c):
        self.cmd(f"R {x} {y} {w} {h} {c}")


def valid(samples):
    return [s for s in samples if ZMIN < s[3] < ZMAX]


def idle_test(L, sec=5):
    L.clear()
    L.text(8, 20, "1) IDLE TEST", 2, CYAN)
    L.text(8, 60, "Do NOT touch the screen", 2, YELLOW)
    L.text(8, 90, f"for {sec} seconds...", 2, YELLOW)
    L.take()
    time.sleep(sec)
    zs = L.take()
    v = valid(zs)
    res = {"lines": len(zs), "valid_press": len(v)}
    if zs:
        res["x_med"] = st.median(s[1] for s in zs)
        res["y_med"] = st.median(s[2] for s in zs)
        res["z_med"] = st.median(s[3] for s in zs)
    if len(v) > 3:
        res["verdict"] = "NG: 触っていないのに押下判定が出る (膜の常時接触/短絡の疑い)"
    elif len(zs) > 20:
        res["verdict"] = "WARN: 押下判定は出ないが値が出続ける (X/Y膜が電気的につながっている疑い)"
    else:
        res["verdict"] = "OK"
    return res


def press_point(L, i, tx, ty, timeout=12):
    """+ を表示し、押して離すまでの有効サンプルを集める"""
    L.clear()
    L.text(8, 8, f"2) PRESS + ({i + 1}/9)", 2, CYAN)
    L.text(8, 200 if ty < 240 else 120, "Press and hold ~1s,", 1, GRAY)
    L.text(8, 212 if ty < 240 else 132, "then release. Stylus OK.", 1, GRAY)
    L.cross(tx, ty)
    L.take()
    got, last_valid, t0 = [], None, time.monotonic()
    while time.monotonic() - t0 < timeout:
        time.sleep(0.05)
        v = valid(L.take())
        if v:
            got += v
            last_valid = time.monotonic()
        elif got and last_valid and time.monotonic() - last_valid > 0.4 and len(got) >= 15:
            break           # 十分取れて、指が離れた
    if not got:
        L.fill(tx - 16, ty - 16, 33, 33, RED)
        return None
    # 押し始め/離し際の不安定な部分を捨てる
    core = got[len(got) // 5: len(got) - len(got) // 5] or got
    xs, ys, zs = [s[1] for s in core], [s[2] for s in core], [s[3] for s in core]
    r = {"tx": tx, "ty": ty, "n": len(got),
         "x": st.median(xs), "y": st.median(ys), "z": st.median(zs),
         "sx": st.pstdev(xs), "sy": st.pstdev(ys)}
    L.fill(tx - 16, ty - 16, 33, 33, GREEN if max(r["sx"], r["sy"]) < 8 else YELLOW)
    time.sleep(0.3)
    return r


def analyze(pts):
    """9点の結果を判定。pts[row*3+col]"""
    out = []
    dead = [i for i, p in enumerate(pts) if p is None]
    if dead:
        out.append(("NG", f"反応なしの点: {[i + 1 for i in dead]} (不感帯/断線の疑い)", f"no response: {[i + 1 for i in dead]}"))
    if len(dead) > 4:
        return out
    g = lambda r, c: pts[r * 3 + c]
    # ノイズ
    noisy = [i + 1 for i, p in enumerate(pts) if p and max(p["sx"], p["sy"]) >= 8]
    if noisy:
        out.append(("WARN", f"値が揺れる点: {noisy} (接触不安定/ノイズ)", f"noisy: {noisy}"))
    # 軸の向き・範囲
    for r in range(3):
        row = [g(r, c) for c in range(3)]
        if all(row):
            xs = [p["x"] for p in row]
            if not (xs[0] > xs[1] > xs[2] or xs[0] < xs[1] < xs[2]):
                out.append(("NG", f"行{r + 1}: 左→右で X 生値が単調でない {xs} (膜の損傷/局所短絡)", f"row{r + 1} X not monotonic"))
    for c in range(3):
        col = [g(r, c) for r in range(3)]
        if all(col):
            ys = [p["y"] for p in col]
            if not (ys[0] > ys[1] > ys[2] or ys[0] < ys[1] < ys[2]):
                out.append(("NG", f"列{c + 1}: 上→下で Y 生値が単調でない {ys}", f"col{c + 1} Y not monotonic"))
    # 範囲(感度)とゆがみ
    def span(a, b):
        return abs(a - b) if a is not None and b is not None else None
    xr = [span(g(r, 0) and g(r, 0)["x"], g(r, 2) and g(r, 2)["x"]) for r in range(3)]
    yr = [span(g(0, c) and g(0, c)["y"], g(2, c) and g(2, c)["y"]) for c in range(3)]
    xr, yr = [v for v in xr if v], [v for v in yr if v]
    if xr and min(xr) < 250:
        out.append(("NG", f"X の生値の幅が狭い {xr} (正常は概ね400以上。膜の一部が常時接触の疑い)", f"X range small {xr}"))
    if yr and min(yr) < 350:
        out.append(("NG", f"Y の生値の幅が狭い {yr} (正常は概ね550以上)", f"Y range small {yr}"))
    if xr:
        for c in range(3):
            col = [g(r, c)["x"] for r in range(3) if g(r, c)]
            if len(col) >= 2:
                skew = (max(col) - min(col)) / (st.mean(xr) / 2)
                if skew > 0.25:
                    out.append(("WARN" if skew < 0.5 else "NG",
                                f"列{c + 1}: 縦位置で X が {max(col) - min(col):.0f} ずれる "
                                f"(列間隔の {skew * 100:.0f}%。膜の抵抗むら)", f"col{c + 1} X skew {skew * 100:.0f}%"))
    if yr:
        for r in range(3):
            row = [g(r, c)["y"] for c in range(3) if g(r, c)]
            if len(row) >= 2:
                skew = (max(row) - min(row)) / (st.mean(yr) / 2)
                if skew > 0.25:
                    out.append(("WARN" if skew < 0.5 else "NG",
                                f"行{r + 1}: 横位置で Y が {max(row) - min(row):.0f} ずれる "
                                f"(行間隔の {skew * 100:.0f}%)", f"row{r + 1} Y skew {skew * 100:.0f}%"))
    # 中央の非線形
    if all(g(1, c) for c in range(3)):
        x0, x1, x2 = (g(1, c)["x"] for c in range(3))
        dev = abs(x1 - (x0 + x2) / 2) / (abs(x2 - x0) / 2)
        if dev > 0.15:
            out.append(("WARN", f"中段の X が中央で {dev * 100:.0f}% 片寄る (非線形。9点校正で補正可)", f"X nonlinear center {dev * 100:.0f}%"))
    if not out:
        out.append(("OK", "大きな異常なし", "no major problem"))
    return out


def free_draw(L, pts, sec=20):
    """9点の結果から行/列ごとの線形補間で仮に位置合わせし、なぞった跡を描く"""
    ok = [p for p in pts if p]
    if len(ok) < 4:
        return
    def fit(raw, true):
        n = len(raw); mr, mt = sum(raw) / n, sum(true) / n
        var = sum((r - mr) ** 2 for r in raw) or 1
        a = sum((r - mr) * (t - mt) for r, t in zip(raw, true)) / var
        return a, mt - a * mr
    ax, bx = fit([p["x"] for p in ok], [p["tx"] for p in ok])
    ay, by = fit([p["y"] for p in ok], [p["ty"] for p in ok])
    L.clear()
    L.text(8, 8, f"3) FREE DRAW {sec}s", 2, CYAN)
    L.text(8, 32, "Trace straight lines along edges.", 1, GRAY)
    L.text(8, 44, "Wavy/jumping dots = film problem.", 1, GRAY)
    for gx in COLS:
        L.cmd(f"L {gx} 60 {gx} {H - 1} {0x2104}")
    for gy in ROWS:
        L.cmd(f"L 0 {gy} {W - 1} {gy} {0x2104}")
    L.take()
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        time.sleep(0.03)
        for _, x, y, z in valid(L.take())[-3:]:
            px, py = int(ax * x + bx), int(ay * y + by)
            if 0 <= px < W and 60 <= py < H:
                L.fill(px - 1, py - 1, 3, 3, YELLOW)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-draw", action="store_true")
    a = ap.parse_args()
    if subprocess.run(["pgrep", "-f", "wifi_ui.py"], capture_output=True).returncode == 0:
        print("wifi_ui.py が動いています。先に: sudo systemctl stop wifi-ui; sudo pkill -f wifi_ui.py")
        return 1
    L = Link(PORT)
    for _ in range(20):           # 疎通
        try:
            L.cmd("P", 0.5)
            break
        except IOError:
            pass
    else:
        print("328P が応答しません (配線/電源/ファームを確認)")
        return 1
    L.cmd("M 0")                  # 押下イベントは使わない
    L.cmd("Z")                    # 生値の連続出力を ON
    report = {"time": datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        print("1) 無操作テスト: 5秒間 画面に触らないでください")
        report["idle"] = idle_test(L)
        print("   ", report["idle"])
        print("2) 9点テスト: 画面の + を順に押して離してください (各12秒まで)")
        pts = [press_point(L, r * 3 + c, COLS[c], ROWS[r], ) for r in range(3) for c in range(3)]
        report["points"] = pts
        res = analyze(pts)
        if report["idle"]["verdict"] != "OK":
            lv = "NG" if report["idle"]["verdict"].startswith("NG") else "WARN"
            res.insert(0, (lv, "無操作: " + report["idle"]["verdict"],
                           "idle: pressed w/o touch" if lv == "NG" else "idle: values w/o touch"))
        report["result"] = res
        print("\n   点  目標(x,y)  生値X  生値Y   z   揺れX/Y  件数")
        for i, p in enumerate(pts):
            if p:
                print(f"   {i + 1}  ({p['tx']:3d},{p['ty']:3d})  {p['x']:5.0f} {p['y']:5.0f} {p['z']:5.0f}"
                      f"  {p['sx']:4.1f}/{p['sy']:4.1f}  {p['n']}")
            else:
                print(f"   {i + 1}  ({COLS[i % 3]:3d},{ROWS[i // 3]:3d})  反応なし")
        print("\n判定:")
        for lv, msg, _ in res:
            print(f"  [{lv}] {msg}")
        if not a.no_draw:
            print("\n3) 自由描画 20秒: 画面の端や格子線に沿ってなぞってください")
            free_draw(L, pts)
        # 結果を画面に
        L.clear()
        worst = "NG" if any(r[0] == "NG" for r in res) else "WARN" if any(r[0] == "WARN" for r in res) else "OK"
        L.text(8, 10, "TOUCH CHECK: " + worst, 3, {"OK": GREEN, "WARN": YELLOW}.get(worst, RED))
        y = 60
        for lv, _, en in res[:14]:
            L.text(8, y, f"[{lv}] {en}"[:50], 1, {"OK": GREEN, "WARN": YELLOW}.get(lv, RED))
            y += 14
        L.text(8, H - 20, "details: see terminal", 1, GRAY)
    finally:
        try:
            L.cmd("Z")            # 生値出力を OFF に戻す
            L.cmd("M 1")
        except IOError:
            pass
        L.alive = False
    path = os.path.expanduser(f"~/touch_check_{datetime.datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n保存: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
