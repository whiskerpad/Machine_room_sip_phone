# キャプティブWi-Fi 解析ツール（Windows PC用）

Piのハードとは関係なく、ノートPCだけでポータルの解析と認証テストができます。
ここで認証まで通れば、`captive_lib.py` をそのままPiへ移植します。

## 準備（初回だけ）
1. Pythonを入れる。PowerShellで `winget install Python.Python.3.12` を実行するか、python.org からインストールする。
2. `G:\sipphone\pc_tools` をノートPCへコピーする。追加ライブラリは不要（標準ライブラリだけで動く）。
3. PowerShellでそのフォルダへ移動する（`cd <コピー先>\pc_tools`）。

## 現地での手順（1か所あたり10分程度）

### ① 自動記録（ブラウザを開く前に行う）
1. Windowsで対象のフリーWi-Fiに接続する。**「ログインが必要です」の通知やブラウザは開かない。**
2. 次を実行する。
   ```
   py captive_probe.py
   ```
3. 画面に、判定結果（ONLINE / PORTAL / NO_NET）、リダイレクトの経路、最後のページにあるフォームの項目が表示される。
4. 結果は `captive_logs\<日時>_<SSID>\` に保存される。

### ② 認証テスト（「同意する」だけのポータル向け）
```
py captive_auth.py --dry-run     ← 送信する内容を表示するだけ（実際には送らない）
py captive_auth.py               ← 実際に送信して、つながるか試す
```
- 「★認証成功」と出れば、Piでも同じ方法で自動認証できる。
- メールアドレスなどの入力欄がある場合は、「入力が必要な欄」と表示されて送信しない。値を入れるときは `--set email=xxx@example.com` を付ける。
- テストをやり直すときは、Windowsの設定でそのSSIDを「削除（忘れる）」してから接続し直す。

### ③ ブラウザで記録する（②が失敗したとき、またはフォームが無いとき）
1. SSIDを削除して接続し直す。
2. ChromeかEdgeを開いて F12 → **Network** タブ → **Preserve log**（ブラウザのバージョンによっては **Keep log**）にチェックを入れる。
3. アドレス欄に `http://neverssl.com` を入力してポータルを表示させ、普段どおりに認証する。
4. Networkタブ上部の下向き矢印（Export HAR）→ **Export HAR (sanitized)** を選ぶ。古い版では一覧を右クリック → **Save all as HAR with content**。`portal.har` として保存する。
   （sanitized はクッキーなどを除いた版。送信したフォームの内容は残るので、解析はこれで足りる）
5. 要約する（メールアドレス・氏名・パスワードらしき値は *** に伏せられる）。
   ```
   py har_summary.py portal.har
   ```
   `portal.har.txt` ができる。**Claudeに渡すのはこの .txt と①の run.json。** HAR本体にはクッキーなどが残るので、渡さない。

## 対応済みのポータル（専用レシピ）
| 名前 | 見分け方 | 認証方法 |
|---|---|---|
| wi2-onetap | `service.wi2.ne.jp/freewifi/...`（ドン・キホーテなど） | JSONで `POST /wi2auth/xhr/login` `{"login_method":"onetap","login_params":{"agree":"1"}}` |

`captive_auth.py` は、転送先の中に既知のポータルを見つけると自動でレシピを使う。レシピを使わずに試すときは `--generic` を付ける。

## 注意
- Windowsの「ランダムなハードウェアアドレス」が有効だと、SSIDごと・接続ごとにMACアドレスが変わることがある。認証テストでは「削除 → 再接続」を1回ずつにそろえること。
- 施設の利用規約の範囲で使うこと（自動で「同意」を送る処理は、自分がブラウザで行う操作と同じ内容）。

## ファイル
| ファイル | 役割 |
|---|---|
| captive_lib.py | 共通処理（判定、リダイレクトの追跡、フォームの解析と送信）。Piへ移植する |
| captive_probe.py | ① 自動記録 |
| captive_auth.py | ② 認証テスト |
| har_summary.py | ③ HARの要約（個人情報を伏せる） |
