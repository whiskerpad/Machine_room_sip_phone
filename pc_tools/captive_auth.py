"""captive_auth.py - 「同意する」型のポータルをブラウザなしで認証できるか試す (Windows/Linux)

使い方:
    py captive_auth.py --dry-run            送信内容を表示するだけ(送らない)
    py captive_auth.py                      最終ページの form[0] を送信して認証を試す
    py captive_auth.py --form 1             2番目のフォームを使う
    py captive_auth.py --set email=a@b.jp   テキスト欄に値を入れる(複数指定可)
結果: ./captive_logs/<日時>_auth/auth.json と各ページの html
※テストのたびに Windows の Wi-Fi 設定でそのSSIDを「削除(忘れる)」→再接続すると未認証状態に戻せる
"""
import argparse
import datetime
import json
import os
import sys
import time

import captive_lib as cl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", type=int, default=0)
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--generic", action="store_true", help="レシピを使わず汎用フォーム送信を試す")
    ap.add_argument("--probe-url", help="検出URLを直接指定(テスト用)")
    ap.add_argument("--out", default="captive_logs")
    a = ap.parse_args()
    probe_name = "google"
    if a.probe_url:
        cl.PROBE_URLS["custom"] = (a.probe_url, 204, None)
        probe_name = "custom"

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(a.out, f"{ts}_auth")
    os.makedirs(outdir, exist_ok=True)
    sess = cl.Session()
    log = {"time": ts}

    r = cl.probe(sess, probe_name)
    print(f"事前判定: {r['state']}")
    if r["state"] != "PORTAL":
        print("ポータルに捕まっていません(ONLINE なら認証済み、NO_NET なら未接続)。終了します。")
        return 0

    hops = cl.follow(sess, cl.PROBE_URLS[probe_name][0])
    log["hops"] = [cl.hop_summary(h) for h in hops]
    for i, h in enumerate(hops):
        print(f"[{i}] {h.get('status')} {h.get('url')}")
    last = hops[-1]

    name, recipe = (None, None) if a.generic else cl.find_recipe(hops)
    if recipe:
        print(f"\nポータル判定: {name}  (専用レシピで認証)")
        plan, ok, res = recipe(sess, hops, dry_run=a.dry_run)
        for m, u, b in plan:
            print(f"    {m} {u}" + (f"  body={json.dumps(b, ensure_ascii=False)}" if b else ""))
        log.update(recipe=name, plan=plan)
        if a.dry_run:
            print("\n--dry-run のため送信しません。")
            cl.dump_json(os.path.join(outdir, "auth.json"), log)
            return 0
        log["recipe_hops"] = [cl.hop_summary(h) | {"body": (h.get("body") or "")[:500]} for h in res]
        for h in res:
            print(f"送信後 {h.get('status')} {h.get('url')}  {(h.get('body') or '')[:120]!r}")
        print(f"レシピ応答: {'result:true' if ok else '失敗'}")
        return finish(log, outdir, probe_name)

    forms = cl.parse_forms(last.get("body"), last.get("url", ""))
    if not forms:
        print("フォームがありません → JavaScript で送信している可能性。HAR を記録して har_summary.py で解析してください。")
        cl.dump_json(os.path.join(outdir, "auth.json"), log)
        return 1
    if a.form >= len(forms):
        print(f"--form {a.form} はありません(フォーム数 {len(forms)})")
        return 1
    form = forms[a.form]
    overrides = dict(s.split("=", 1) for s in a.set)
    data, missing = cl.build_submission(form, overrides)
    print(f"\n送信先: {form['method']} {form['action_url']}")
    for k, v in data:
        print(f"    {k} = {v}")
    log.update(form=form, data=data, missing=missing)
    if missing:
        print(f"\n入力が必要な欄があります: {missing}\n  --set 名前=値 で指定してください。送信しません。")
        cl.dump_json(os.path.join(outdir, "auth.json"), log)
        return 1
    if a.dry_run:
        print("\n--dry-run のため送信しません。")
        cl.dump_json(os.path.join(outdir, "auth.json"), log)
        return 0

    res = cl.submit(sess, form, data)
    log["submit_hops"] = [cl.hop_summary(h) for h in res]
    for i, h in enumerate(res):
        print(f"送信後[{i}] {h.get('status')} {h.get('url')}")
        with open(os.path.join(outdir, f"after_{i}.html"), "w", encoding="utf-8") as f:
            f.write(h.get("body") or "")

    return finish(log, outdir, probe_name)


def finish(log, outdir, probe_name):
    state = "PORTAL"
    for _ in range(5):  # 反映に数秒かかるポータルがある
        time.sleep(1.5)
        state = cl.probe(cl.Session(), probe_name)["state"]
        if state == "ONLINE":
            break
    log["result"] = state
    cl.dump_json(os.path.join(outdir, "auth.json"), log)
    print(f"\n結果: {state}  {'★認証成功' if state == 'ONLINE' else '→ 失敗。HAR と見比べて送信内容の差を確認'}")
    print(f"保存先: {os.path.abspath(outdir)}")
    return 0 if state == "ONLINE" else 1


if __name__ == "__main__":
    sys.exit(main())
