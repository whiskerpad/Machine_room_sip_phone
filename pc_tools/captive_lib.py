"""captive_lib.py - キャプティブポータル解析・認証の共通処理 (標準ライブラリのみ)

Windows PC と Raspberry Pi の両方で動く。Pi へはこのファイルをそのまま移植する想定。
"""
import html
import http.cookiejar
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
PROBE_URLS = {
    "google": ("http://connectivitycheck.gstatic.com/generate_204", 204, None),
    "apple": ("http://captive.apple.com/hotspot-detect.html", 200, "Success"),
    "msft": ("http://www.msftconnecttest.com/connecttest.txt", 200, "Microsoft Connect Test"),
}
REDIRECT_CODES = (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """リダイレクトを自動で追わない(1ホップずつ記録するため)"""

    def redirect_request(self, *a, **k):
        return None


class Session:
    """クッキーを保持する HTTP セッション(プロキシ不使用)"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()

    def _opener(self, verify):
        ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=ctx),
            _NoRedirect())

    def request(self, url, method="GET", data=None, headers=None, timeout=12, json_body=None):
        """1回だけ送る。戻り値 dict: url, method, status, headers, body, cert_ok, error"""
        hdr = {"User-Agent": UA, "Accept": "text/html,*/*"}
        hdr.update(headers or {})
        body_bytes = None
        if json_body is not None:
            body_bytes = json.dumps(json_body, separators=(",", ":")).encode()
            hdr["Content-Type"] = "application/json"
            data = json_body
        elif data is not None:
            body_bytes = urllib.parse.urlencode(data).encode()
            hdr.setdefault("Content-Type", "application/x-www-form-urlencoded")
        res = {"url": url, "method": method, "sent": data}
        for verify in (True, False):
            req = urllib.request.Request(url, data=body_bytes, headers=hdr, method=method)
            try:
                with self._opener(verify).open(req, timeout=timeout) as r:
                    res.update(status=r.status, headers=r.getheaders(),
                               body=_decode(r.read(800000), r.headers), cert_ok=verify)
                return res
            except urllib.error.HTTPError as e:  # 3xx/4xx/5xx もここに来る
                res.update(status=e.code, headers=list(e.headers.items()) if e.headers else [],
                           body=_decode(e.read(800000), e.headers), cert_ok=verify)
                return res
            except Exception as e:
                res["error"] = repr(e)
                if verify and "CERTIFICATE" in repr(e).upper():
                    continue  # 時計ずれ/自己署名 → 記録目的で検証なし再試行
                return res
        return res

    def cookies(self):
        return [{"domain": c.domain, "name": c.name, "path": c.path} for c in self.jar]


def _decode(raw, headers):
    cs = None
    if headers is not None:
        cs = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    for enc in filter(None, (cs, "utf-8", "cp932", "euc-jp", "latin-1")):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("latin-1", "replace")


def header(res, name):
    for k, v in res.get("headers") or []:
        if k.lower() == name.lower():
            return v
    return None


# ------------------------------------------------------------------ 判定 ----
def probe(sess, name="google"):
    """ONLINE / PORTAL / NO_NET を返す(リダイレクトは追わない)"""
    url, code, text = PROBE_URLS[name]
    r = sess.request(url)
    if "error" in r and "status" not in r:
        r["state"] = "NO_NET"
    elif r["status"] == code and (text is None or text in (r.get("body") or "")) \
            and (code != 204 or not (r.get("body") or "").strip()):
        r["state"] = "ONLINE"
    else:
        r["state"] = "PORTAL"
    return r


JS_REDIRECTS = [
    r'http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*url=([^"\'>\s]+)',
    r'(?:window|document|top|self)\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
    r'location\.(?:replace|assign)\(\s*["\']([^"\']+)["\']',
]


def page_redirect(body):
    """meta refresh / JavaScript によるページ遷移先を探す"""
    for pat in JS_REDIRECTS:
        m = re.search(pat, body or "", re.I)
        if m:
            return html.unescape(m.group(1))
    return None


def follow(sess, url, max_hops=10):
    """HTTP リダイレクトと meta/JS リダイレクトを1ホップずつ追う。hops のリストを返す"""
    hops, seen = [], set()
    method, data = "GET", None
    while url and len(hops) < max_hops and url not in seen:
        seen.add(url)
        r = sess.request(url, method, data)
        hops.append(r)
        method, data = "GET", None
        if r.get("status") in REDIRECT_CODES and header(r, "Location"):
            url = urllib.parse.urljoin(url, header(r, "Location"))
            r["next"], r["via"] = url, "http"
            continue
        nxt = page_redirect(r.get("body")) if r.get("status") == 200 else None
        if nxt:
            url = urllib.parse.urljoin(url, nxt)
            r["next"], r["via"] = url, "page"
            continue
        break
    return hops


# ---------------------------------------------------------------- フォーム ----
class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self.cur, self.sel, self.ta = [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            self.cur = {"action": a.get("action", ""), "method": (a.get("method") or "GET").upper(),
                        "id": a.get("id"), "fields": []}
            self.forms.append(self.cur)
        elif self.cur is None:
            return
        elif tag == "input":
            self.cur["fields"].append({"tag": "input", "type": (a.get("type") or "text").lower(),
                                       "name": a.get("name"), "value": a.get("value", ""),
                                       "checked": "checked" in a, "id": a.get("id"),
                                       "required": "required" in a})
        elif tag == "button":
            self.cur["fields"].append({"tag": "button", "type": (a.get("type") or "submit").lower(),
                                       "name": a.get("name"), "value": a.get("value", ""),
                                       "id": a.get("id")})
        elif tag == "select":
            self.sel = {"tag": "select", "type": "select", "name": a.get("name"), "options": []}
            self.cur["fields"].append(self.sel)
        elif tag == "option" and self.sel is not None:
            self.sel["options"].append(a.get("value", ""))
        elif tag == "textarea":
            self.ta = {"tag": "textarea", "type": "textarea", "name": a.get("name"), "value": ""}
            self.cur["fields"].append(self.ta)

    def handle_endtag(self, tag):
        if tag == "form":
            self.cur = None
        elif tag == "select":
            self.sel = None
        elif tag == "textarea":
            self.ta = None

    def handle_data(self, data):
        if self.ta is not None:
            self.ta["value"] += data


def parse_forms(body, base_url):
    p = _FormParser()
    try:
        p.feed(body or "")
    except Exception:
        pass
    for f in p.forms:
        f["action_url"] = urllib.parse.urljoin(base_url, f["action"] or base_url)
    return p.forms


TEXT_TYPES = ("text", "email", "tel", "password", "number", "textarea", "search", "url", "date")


def build_submission(form, overrides=None):
    """ブラウザが送るはずの値を組み立てる。
    hidden はそのまま、checkbox は「同意」とみなして全てオン、radio は選択済み(なければ先頭)、
    select は先頭の選択肢、送信ボタンは先頭の1つ。テキスト系は overrides が無ければ missing に入れる。"""
    overrides = dict(overrides or {})
    data, missing, radios, submit_done = [], [], set(), False
    for f in form["fields"]:
        name, t = f.get("name"), f.get("type")
        if not name:
            continue
        if name in overrides:
            if t in ("submit", "image", "button") or f["tag"] == "button":
                continue
            data.append((name, overrides.pop(name)))
            continue
        if t == "hidden":
            data.append((name, f["value"]))
        elif t == "checkbox":
            data.append((name, f["value"] or "on"))
        elif t == "radio":
            if name not in radios and (f.get("checked") or not any(
                    g.get("name") == name and g.get("checked") for g in form["fields"])):
                data.append((name, f["value"]))
                radios.add(name)
        elif t == "select":
            data.append((name, f["options"][0] if f["options"] else ""))
        elif t in ("submit", "image") or (f["tag"] == "button" and t == "submit"):
            if not submit_done:
                data.append((name, f.get("value", "")))
                submit_done = True
        elif t in TEXT_TYPES:
            if f.get("value"):
                data.append((name, f["value"]))
            else:
                missing.append(name)
    data += list(overrides.items())  # フォームに無い追加項目
    return data, missing


def submit(sess, form, data):
    url = form["action_url"]
    if form["method"] == "POST":
        return follow_from(sess, url, "POST", data)
    q = urllib.parse.urlencode(data)
    return follow_from(sess, url + ("&" if "?" in url else "?") + q, "GET", None)


def follow_from(sess, url, method, data, max_hops=8):
    first = sess.request(url, method, data)
    hops = [first]
    if first.get("status") in REDIRECT_CODES and header(first, "Location"):
        nxt = urllib.parse.urljoin(url, header(first, "Location"))
        first["next"], first["via"] = nxt, "http"
        hops += follow(sess, nxt, max_hops)
    return hops


# -------------------------------------------------------- ポータル別レシピ ----
# フォームを使わず JavaScript(XHR) で認証するポータルは、HAR で確認した通信を直接再現する。

def _is_wi2(hops):
    for h in hops:
        u, b = h.get("url") or "", h.get("body") or ""
        if "wi2.ne.jp" in u or "wi2-onetap-login-button" in b or "wi2login" in b or "/wi2auth/" in b:
            return True
        if "/freewifi/" in u and "wi2" in b.lower():
            return True
    return False


def recipe_wi2_onetap(sess, hops, dry_run=False):
    """Wi2 (株式会社ワイヤ・アンド・ワイヤレス) の「同意して接続」型。
    2026-06 ドン・キホーテで HAR 確認:
      agreement.html の Accept ボタン → POST /wi2auth/xhr/login
      JSON {"login_method":"onetap","login_params":{"agree":"1"}} → {"result":true}
    ドンキ以外の Wi2 系フリーWi-Fi も同じ仕組みの可能性が高い。"""
    base = next((h["url"] for h in reversed(hops) if "/freewifi/" in (h.get("url") or "")),
                hops[-1].get("url", ""))
    pu = urllib.parse.urlsplit(base)
    origin = f"{pu.scheme}://{pu.netloc}"
    agree_url = urllib.parse.urljoin(base, "agreement.html")
    login_url = origin + "/wi2auth/xhr/login"
    body = {"login_method": "onetap", "login_params": {"agree": "1"}}
    plan = [("GET", agree_url, None), ("POST", login_url, body)]
    if dry_run:
        return plan, None, []
    r1 = sess.request(agree_url)
    r2 = sess.request(login_url, "POST", json_body=body, headers={
        "X-Requested-With": "XMLHttpRequest", "Origin": origin, "Referer": agree_url,
        "Accept": "application/json, text/javascript, */*; q=0.01"})
    ok = '"result":true' in (r2.get("body") or "").replace(" ", "")
    return plan, ok, [r1, r2]


RECIPES = [("wi2-onetap", _is_wi2, recipe_wi2_onetap)]


def find_recipe(hops):
    for name, match, fn in RECIPES:
        if match(hops):
            return name, fn
    return None, None


# ------------------------------------------------------------------ 記録 ----
def hop_summary(h):
    return {k: h.get(k) for k in ("method", "url", "status", "via", "next", "cert_ok", "error")} | {
        "location": header(h, "Location"),
        "set_cookie": [v.split("=")[0] for k, v in (h.get("headers") or []) if k.lower() == "set-cookie"],
        "body_len": len(h.get("body") or ""),
    }


def dump_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
