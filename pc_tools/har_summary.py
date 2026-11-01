"""har_summary.py - ブラウザで保存した HAR から、認証に関係する通信だけを要約する

使い方:  py har_summary.py portal.har            → portal.har.txt を出力
         py har_summary.py portal.har --no-mask   個人情報のマスクをしない(自分で見るとき用)
メール/電話/氏名/パスワードらしき項目の値は既定で *** に置き換える。
"""
import argparse
import json
import re
import sys
import urllib.parse

SENSITIVE = re.compile(r"mail|tel|phone|name|pass|pwd|user|login|birth|zip|addr", re.I)
SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|ico|woff2?|ttf|css)(\?|$)", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("har")
    ap.add_argument("--no-mask", action="store_true")
    ap.add_argument("--all", action="store_true", help="画像/CSS も含める")
    a = ap.parse_args()
    with open(a.har, encoding="utf-8-sig") as f:
        har = json.load(f)
    out = []
    for i, e in enumerate(har["log"]["entries"]):
        rq, rs = e["request"], e["response"]
        url = rq["url"]
        if not a.all and SKIP_EXT.search(url):
            continue
        out.append(f"[{i}] {rq['method']} {rs.get('status')} {url}")
        loc = rs.get("redirectURL") or next((h["value"] for h in rs.get("headers", [])
                                             if h["name"].lower() == "location"), "")
        if loc:
            out.append(f"     -> {loc}")
        pd = rq.get("postData")
        if pd:
            params = pd.get("params") or [{"name": k, "value": v} for k, v in
                                          urllib.parse.parse_qsl(pd.get("text", ""), keep_blank_values=True)]
            if params:
                for p in params:
                    v = p.get("value", "")
                    if not a.no_mask and SENSITIVE.search(p["name"]) and v:
                        v = "***"
                    out.append(f"     POST {p['name']} = {v[:80]}")
            elif pd.get("text"):
                out.append(f"     POST body({pd.get('mimeType')}): {pd['text'][:200]}")
        sc = [h["value"].split("=")[0] for h in rs.get("headers", []) if h["name"].lower() == "set-cookie"]
        if sc:
            out.append(f"     set-cookie: {', '.join(sc)}")
    path = a.har + ".txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n".join(out))
    print(f"\n保存: {path}")


if __name__ == "__main__":
    sys.exit(main())
