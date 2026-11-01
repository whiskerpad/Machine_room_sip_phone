"""captive_probe.py - 今つながっている Wi-Fi のキャプティブポータルを調べて記録する (Windows/Linux)

使い方:  PC を対象の Wi-Fi に接続してから(ブラウザのポータル画面は閉じたまま)
    py captive_probe.py
結果: ./captive_logs/<日時>_<SSID>/ に run.json(要約) と hop_*.html(各ページ) を保存
"""
import argparse
import datetime
import os
import platform
import re
import subprocess
import sys

import captive_lib as cl


def wifi_info():
    """接続中の SSID / BSSID / 自分の MAC を取得(Windows は netsh)"""
    info = {"raw": ""}
    try:
        if platform.system() == "Windows":
            raw = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True).stdout
            # コンソールのコードページにより UTF-8 / CP932 のどちらでも出るので両方試す
            try:
                out = raw.decode("utf-8")
            except UnicodeDecodeError:
                out = raw.decode("cp932", errors="replace")
            info["raw"] = out
            for line in out.splitlines():
                k, _, v = line.partition(" : ")
                k, v = k.strip(), v.strip()
                if re.fullmatch(r"SSID", k):
                    info["ssid"] = v
                elif k == "BSSID" or k.startswith("AP BSSID"):
                    info["bssid"] = v
                elif "物理アドレス" in k or k == "Physical address":
                    info["mac"] = v
                elif k in ("認証", "Authentication"):
                    info["auth"] = v
        else:
            out = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID", "dev", "wifi"],
                                 capture_output=True, text=True).stdout
            info["raw"] = out
            for line in out.splitlines():
                if line.startswith("yes:"):
                    parts = line.replace("\\:", "\x00").split(":")
                    info["ssid"] = parts[1].replace("\x00", ":")
    except Exception as e:
        info["error"] = repr(e)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default="google", choices=list(cl.PROBE_URLS),
                    help="ポータル検出に使うURL")
    ap.add_argument("--probe-url", help="検出URLを直接指定(テスト用)")
    ap.add_argument("--out", default="captive_logs")
    a = ap.parse_args()
    if a.probe_url:
        cl.PROBE_URLS["custom"] = (a.probe_url, 204, None)
        a.probe = "custom"

    wi = wifi_info()
    ssid = wi.get("ssid", "unknown")
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(a.out, f"{ts}_{re.sub(r'[^A-Za-z0-9._-]', '_', ssid)[:32]}")
    os.makedirs(outdir, exist_ok=True)
    print(f"SSID: {ssid}   MAC: {wi.get('mac', '?')}   BSSID: {wi.get('bssid', '?')}")

    sess = cl.Session()
    probes = {}
    for name in ([a.probe] if a.probe == "custom" else list(cl.PROBE_URLS)):
        r = cl.probe(sess, name)
        probes[name] = cl.hop_summary(r) | {"state": r["state"]}
        print(f"probe {name:6s}: {r['state']:7s} status={r.get('status')} "
              f"location={cl.header(r, 'Location')} {r.get('error', '')}")
    state = probes[a.probe]["state"]

    hops, forms = [], []
    if state == "PORTAL":
        print("\n--- リダイレクトの追跡 ---")
        hops = cl.follow(cl.Session(), cl.PROBE_URLS[a.probe][0])
        for i, h in enumerate(hops):
            print(f"[{i}] {h.get('method')} {h.get('status')} {h.get('url')}")
            if h.get("next"):
                print(f"     -> ({h['via']}) {h['next']}")
            if h.get("error"):
                print(f"     error: {h['error']}")
            with open(os.path.join(outdir, f"hop_{i}.html"), "w", encoding="utf-8") as f:
                f.write(h.get("body") or "")
        last = hops[-1] if hops else {}
        rname, _ = cl.find_recipe(hops)
        if rname:
            print(f"\n★ 既知のポータル: {rname}  → captive_auth.py で専用レシピを使って認証できる見込み")
        forms = cl.parse_forms(last.get("body"), last.get("url", ""))
        print(f"\n--- 最終ページのフォーム: {len(forms)} 個 ---")
        for i, fm in enumerate(forms):
            print(f"form[{i}] {fm['method']} {fm['action_url']}")
            for fl in fm["fields"]:
                print(f"    {fl['tag']:8s} type={fl.get('type')!s:9s} name={fl.get('name')!s:20s} "
                      f"value={str(fl.get('value', fl.get('options', '')))[:40]}")
            _, missing = cl.build_submission(fm)
            if missing:
                print(f"    ※入力が必要な欄: {missing}")
        scripts = len(__import__("re").findall(r"<script", last.get("body") or "", re.I))
        print(f"\n<script> タグ: {scripts} 個  (多い/フォームが無い → JavaScript 必須の可能性。HAR で解析)")

    cl.dump_json(os.path.join(outdir, "run.json"), {
        "time": ts, "wifi": wi, "probes": probes, "state": state,
        "hops": [cl.hop_summary(h) for h in hops], "forms": forms,
        "recipe": cl.find_recipe(hops)[0] if hops else None,
    })
    print(f"\n判定: {state}\n保存先: {os.path.abspath(outdir)}")


if __name__ == "__main__":
    sys.exit(main())
