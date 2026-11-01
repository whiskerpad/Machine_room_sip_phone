# SIP電話端末 開発ログ（試行の経過と詰まった点）

対象: Pi Zero W + ATmega328P(UNO) + 3.95" ILI9488 TFTシールド
記録期間: 2026-09-24 〜 2026-11-02

---

## 現在の状態（2026-11-02 時点）

| 項目 | 状態 |
|---|---|
| TFT（328P 表示コプロセッサ、UART 250000bps） | OK。9点メッシュでタッチを校正。左端の約30pxは精度が悪いため使わない |
| Wi-Fi の選択と接続（TFT） | OK |
| キャプティブポータルの自動認証（ドン・キホーテ / Wi2 onetap） | PC と Pi の両方で成功。60秒ごとに確認し、必要なら再認証 |
| SIPサーバー（Pi Zero W + Kamailio 6.0.1 + Tailscale） | 稼働中。TLSF でないと起動しない |
| PC(MicroSIP 1001) ⇔ iPhone(AGEphone 1002) | 通話成功 |
| 端末Pi（baresip 1003 + USBヘッドセット + TFT通話画面） | 発信・着信・ヘッドセットのボタン操作まで成功 |
| 現地試験（ドン・キホーテで端末から通話） | 未実施 |

---

## 経過

### Step 0: 実現性検討（09-24）
- 構成は「初代Pi Zero端末 + Pi Zeroサーバー（Kamailio）+ Tailscale + AGEphone」。
- 検討の詳細は feasibility-study.md を参照。

### Step 1: TFTの型番確認（09-25）
- MCUFRIEND_kbv の `LCD_ID_readreg` を実行。`reg(0x00D3) = 00 00 94 88` から **ILI9488** と確定した。
- 表示方式は案C（ATmega328P を表示・タッチ専用のコプロセッサにする）を採用。Pi とは UART 250000bps でつなぐ。行単位で命令を送り、`K` の返事を待ってから次を送る。

### Step 2: コード作成（09-25）
- `tft_coproc.ino`（328P側）と `wifi_ui.py`（Pi側、nmcli を使う）を作成。
- クラウド環境で、328P側はコンパイルを確認。Pi側は疑似シリアルでUIの動作を確認した。実機での確認はこの時点ではしていない。

### Step 3: Guest2-Repeater のレビュー（09-25）
- 対象のポータルは Cisco ISE の同意型（ボタンID `ui_aup_accept_button` から判断）。
- Selenium と Chromium を使う方式は Pi Zero では動かせない → requests でフォームを再送信する方式に置き換える方針。

### Step 4: Pi Zero の初期セットアップ（09-25）
| 詰まった点 | 原因 / 対処 |
|---|---|
| USBキーボードでログインできない | Bookworm 以降は初期ユーザー（pi/raspberry）がない。Imager でユーザーを作る。キー配列はUS/UKなので記号がずれる |
| Wi-Fi につながらない | Pi Zero W / Zero 2 W は **2.4GHzのみ**。SSIDの末尾が **-G** のもの（2.4GHz）を選ぶ。-A は5GHz |
| SSHで入れない | 初回起動直後は処理が続いている。**しばらく放置したら入れた**（Pi Zeroの初回起動は遅い） |

- ホスト名は sipphone、IPは 192.168.1.65、ユーザーは pi。

### Step 5: 転送スクリプト（09-25）
- `deploy.ps1` / `deploy.bat` / `pi_install.sh` を作成。scp で転送し、構文チェック後に /opt/sipphone へ設置してサービスを再起動する。
- 1回の実行でパスワードを3回、sudo パスワードも聞かれる → `-Key` で鍵を登録すれば省略できる。
- 初回は「wifi-ui service is not enabled」と出るが正常。`-Setup` で自動起動を有効にする。

### Step 6: Arduino のライブラリとコンパイル（09-25〜26）
| 詰まった点 | 原因 / 対処 |
|---|---|
| TouchScreen.h はどれを使うか | **Adafruit TouchScreen**（Adafruit製）。同名のヘッダを持つ別のライブラリがあると衝突する |
| `'GPIO' was not declared in this scope`（mcufriend_shield.h） | IDEの**ボード選択が ESP32** になっていた → **Arduino Uno** に変更 |
| `grpc: error while marshaling: string field contains invalid UTF-8` | 上のエラーの巻き添え。ライブラリの場所が日本語パス（OneDrive\ドキュメント）で、エラーメッセージを処理できなかった。ボードを直せば出なくなる |

### Step 7: タッチの校正（09-25）
- `TouchScreen_Calibr_native` のピン検出結果: `(A1, D7)=29`, `(A2, D6)=27` → `XP=6, XM=A2, YP=A1, YM=7`
- 縦向き（PORTRAIT）の校正値: `LEFT=691, RT=155, TOP=919, BOT=169`
- 検出画面のあとで十字マークを順に押し続けないと、校正値は出力されない（ピン検出だけで止まっていると勘違いしやすい）。

### Step 8: UNO単体での命令テスト（09-26）
| 詰まった点 | 原因 / 対処 |
|---|---|
| 触っても `D` が返らない（`K` しか来ない） | 判定条件 `z < 1000` が誤りだった。Adafruit の z は接触抵抗なので、軽く触れると1000を超える → 上限をなくし `z > 200` だけで判定。確認用に `Z` コマンドを追加 |
| `READY A0A0` と出て画面が出ない | 電源投入直後の型番読み取りが化けた → 5回までやり直し、それでも駄目なら 0x9488 で強制初期化。修正後は `READY 9488 read=9488` |
| `T` の文字が出ない | 最初は「Waiting for Pi...」と同じ座標・同じ色に描いていて区別できなかった。その後、半角で1行ずつ送ったら C / R / T すべて表示された。原因は、複数行をまとめて貼り付けたか全角スペースだったと推定（確定はしていない） |
| タッチ座標がずれる | 左上で (48,28)、右下は (319,479) で頭打ち → `Z` で四隅の生の値を測って再計算する（**未実施**） |

### Step 9: Pi ⇔ UNO の接続（09-26）
- 仮の構成: UNO + TFTシールドを重ねる。Pi の TX/RX を D0/D1 に接続。Pi の 5V を UNO の **Vin**、GND 同士を接続。
- TFTは「Waiting for Pi」のまま変わらない。
- Pi側の確認結果:
  - `/dev/serial0 -> ttyAMA0` → UARTの設定は正常。
  - wifi_ui が `OSError: TFT coprocessor not responding` で終了と再起動を繰り返している。
- 切り分けテスト: `s.write(b'C 63488\n'); s.read(20)` を実行 → **画面は赤くなるが受信は `b''`**。
  - Pi → UNO は正常。**UNO → Pi が不通**。
- 気づいた点:
  - **Vin に5Vを入れるのは誤り**。Vin はレギュレータを通るので、5V系が約4Vまで下がる → UNOの **5V ピン**に入れる（USBは抜いておく）。
  - UNO→Pi が不通になる原因の候補: D1 → 分圧回路（2.2kΩ/3.3kΩ）→ **物理ピン10** の配線違い、GNDの共通不良。
- 次の確認:
  1. 物理ピン10の待機中の電圧を測る（2.4〜3Vになるはず）。
  2. UNOを外し、物理ピン8と10を直結して折り返しテストする（`b'C 63488\n'` が返ればPi側は正常）。

- **結果（09-26）: 分圧抵抗を入れたら受信できた。** 抵抗が入っていなかったため、D1からの信号がPiまで届いていなかった。

### Step 10: タッチ位置の補正（09-26）
- UIのボタンを押すと、反応する位置が指より右上にずれる（キー0.5個分くらい）。
- wifi_ui.py に補正値 `TOUCH_DX=-16`, `TOUCH_DY=+24`(px) を追加した。キーは32×48pxなので0.5個分。環境変数で上書きできる。
- 328Pを書き換えずに、`deploy.ps1` だけで調整できるようにPi側で補正している。
- 結果: **逆方向にずれた**（ずれの向きを私が読み違えていた）→ 固定のオフセットはやめた。
- 代わりに**タッチ校正モード（CAL）**を追加。一覧画面の右上にある `CAL` から入る。または `sudo python3 /opt/sipphone/wifi_ui.py --cal` で起動する。
  - 5点（四隅から40px内側＋中央）の十字をタップ → 最小二乗で `x'=ax*x+bx`, `y'=ay*y+by` を求める。オフセットだけでなく倍率も補正する。
  - テスト画面では、タップした位置に赤い点が出る → SAVE で `/opt/sipphone/touch_cal.json` に保存する。deploy しても消えない。

### Step 11: タッチの非線形の調査と9点校正（09-27）
- 5点校正の後もずれが残った。1,q,a,z を押すと 3,e,s,z、0,p,-,@ を押すと 9,o,l,. が出る。最下段のボタンは左半分を押すと左隣のボタンになる。
- `Z` で生の値を取得した結果:
  - 中央を押し続けたとき、z が 364→263 と変化しても x は 350〜355 で一定 → **押す強さの影響はない**。
  - 右上の角が x 107〜113、右下の角が x 114〜117 → **X座標がY座標に引っ張られていない**。
  - 中央の生の値 x=352 が画面座標 **201** に変換されていた（正しくは160）→ **X方向が非線形**。端の2点だけで一直線に校正すると、中央で約40pxずれる。
  - Y方向はほぼ線形（上 880 / 中央 485 / 下 116）。
- 詰まった点: 「左上・左下を押した」という報告が、実際には右上・右下だった。生の値と校正値を照合して判明した。
- 対処:
  - 328P: `M 1` で生値モードを追加。押したときに `A rx ry`（ADCの生値）を送る。**要・再書き込み**。
  - Pi: CAL を **3×3＝9点** に変更。軸ごとに「端・中央・端」の3点を結ぶ折れ線で変換する。保存先は `touch_cal.json`（旧形式のファイルは無視して既定値に戻る）。
  - 模擬環境で、非線形な生の値を与えて検証した: テストの点が正しい位置に出る／一覧の2行目を選べる。

### Step 12: 9点メッシュ補正（09-27）
- 軸ごとの9点校正の結果: `x 612>30 373>160 147>290`, `y 859>30 507>240 153>450`。
- テストで押した位置は、上段で +5〜25px、中段の左で **約+60px**、中段の中央で約+15px、中段の右で約+5px（写真から推定）。
  - **左側では、X座標のずれが縦の位置によって変わる**。軸ごとの補正ではこれを直せない。
- 詰まった点: 校正前の既定値が端の2点だけの一直線補正だったため、CALボタンを押すと右隣の OFF が反応した → 既定値を実測3点の折れ線に変更。確実に校正するには `--cal` で起動する。
- 対処: 9点の実測値をそのまま保存（`{"pts": [[rx,ry,sx,sy]×9]}`）し、三角形8枚に分割して三角形ごとに一次変換する方式にした。9点では誤差0で、X座標のずれが縦の位置で変わる歪みにも追従する。
  - 模擬環境で左側を歪ませたモデルを与えたところ、最大誤差は1.4px。
  - テスト画面に、9点の生の値と、最後に押した位置の `raw -> 画面座標` を表示するようにした。

### Step 13: 左端のタッチが波打つ（09-28）
- 詰まった点: 旧版の `--cal` がバックグラウンドで動いたままで、シリアルを取り合っていた（`TFT no ACK: M 1`）→ `sudo pkill -f wifi_ui.py` で解消。
- 9点メッシュ補正の後に 1,Q,A,Z,0,P,-,@ を押すと、1,W,P,-,@ が入力された。大きく改善したが、左端の列（Q, A）がうまく入らない。
- 9点の生の値（行ごと）: `560,859 348,852 140,850 / 511,511 356,502 145,505 / 764,152 434,148 153,145`
  - 左の列の X が 上560 → 中511 → 下764、中央の列も 348 → 356 → 434 と、**下の段ほど X の値が大きい**。
- テスト画面で、左端を上から下へ直線状にタップした → 赤い点が**左右に波打った**（振れ幅はおよそ±20〜25px）。
- 判断: 左右方向を測る抵抗膜（またはその端の電極）の抵抗が均一でない、**パネル個体の不良**の可能性が高い。点と点の間で波打つので、9点の補正では追従できない。

### Step 14: 方針決定と、タッチ精度に合わせた画面設計（09-28）
- 方針: **パネルは買い替えない。画面の作りでカバーする。** それでも無理な場合は、5ピンのロータリーエンコーダ（KY-040 など）で「選択を移動→押して決定」する方式にする。
- パスワード入力を、QWERTY（32×48pxのキー）から**携帯電話式のマルチタップ**（96×62pxのキー）に変更した。
  - 左端30pxにはキーを置かない。同じキーを1.2秒以内に押すと次の文字へ進む。入力途中の文字は黄色で表示する。
  - 1: `.,-_@!?/:1`、2〜9: 英字と数字、0: 空白と0、*: `'"()[]{}<>^\`|~\\`、#: `#$%&*+=;`。Aa で大文字/小文字を切り替える。印字可能なASCII文字はすべて入力できる。
  - 模擬環境で、2を3回→c、3→d、DEL、1を5回→@、0を2回→0 と押し、`c@0` が入力されることを確認した。
- エンコーダ方式にする場合の配線案（未実装）: CLK→GPIO17（物理11）、DT→GPIO27（物理13）、SW→GPIO22（物理15）、+→3.3V（物理1）、GND→物理9。Pi側で gpiozero の RotaryEncoder / Button を使う。

### Step 15: 数字モードと接続処理の見直し（09-28）
- 問題: マルチタップでは、数字を出すまでに何度も押す必要があった（2は4回、1は10回）→ 最下段に **123** ボタンを追加した（Aa / 123 / DEL / OK、各72px）。数字モードでは1回押すとそのキーの数字・*・# が入る。
- 問題: パスワードを入力しても接続できなかった。原因は未確定。候補は次の2つ。
  1. 入力ミス（マルチタップで打ちにくかった）
  2. WPA2/WPA3 混在のAPで、NetworkManager が WPA3(SAE) を選び、Pi Zero W の Wi-Fi チップ（brcmfmac）が失敗した
- 対処:
  - 接続方法を `nmcli device wifi connect` から、**専用プロファイル `sip-<SSID>` を作成して `connection up` する方式**に変更した。
  - WPA2 を含むAPでは `wifi-sec.key-mgmt wpa-psk` を明示し、WPA3 だけのAPなら `sae` を使う。
  - `wifi.cloned-mac-address permanent` を指定し、MACアドレスをランダム化しない（キャプティブポータルはMACで認証状態を管理するため）。
  - 失敗画面に、セキュリティ方式・パスワードの文字数・nmcli のエラーを表示する。
  - 接続の試行を `captive_logs/connect.log` に記録する（パスワード本体は残さず、文字数だけ）。

### Step 16: Wi-Fi接続の成功、PCでのポータル解析へ（09-28）
- 専用プロファイル方式で、自宅APへの接続に成功した。電源は、Piの5VピンからUNOの5Vピンへ供給する方式に変更した（Vinはやめた）。
- 方針: ハードの製作と並行して、**キャプティブポータルの解析はWindowsのノートPCで行う**。
- `pc_tools/` を作成した（標準ライブラリのみ。Windows/Linuxで動く）:
  - `captive_lib.py`: 判定、リダイレクトの追跡（HTTP・meta・JavaScript）、フォームの解析、送信内容の組み立て。**Piへそのまま移植する前提**
  - `captive_probe.py`: 記録する（netsh から SSID/MAC を取得し、run.json と hop_*.html を保存する）
  - `captive_auth.py`: 同意型ポータルの自動認証テスト（`--dry-run` / `--set`）
  - `har_summary.py`: ブラウザのHARを要約する（個人情報らしき値は伏せる）
- 模擬ポータルで検証した（302 → meta refresh → クッキーと隠しトークン付きのフォーム → POST）: probe で PORTAL と判定 → auth で ONLINE になることを確認。

### Step 17: ドン・キホーテのフリーWi-Fi（Wi2）をHARで解析（09-28）
- ブラウザ（Edge）で記録したHAR（sanitized、289件）を解析した。
- ポータルは **Wi2（ワイヤ・アンド・ワイヤレス、service.wi2.ne.jp）の同意型「onetap」**。
  - `/freewifi/donki/index.html` → `agreement.html`（規約。フォームは `action="javascript:void(0)"`、ボタンは `type=button`）
  - Accept を押す → **`POST https://service.wi2.ne.jp/wi2auth/xhr/login`**
    - ヘッダー: `Content-Type: application/json`、`X-Requested-With: XMLHttpRequest`
    - 本文: `{"login_method":"onetap","login_params":{"agree":"1"}}`
    - 応答: `{"result":true,"licensed":null,"message":null}` → これで認証完了
  - その後の `landing.html`、`POST /wi2auth/lpurl`（移動先URLの取得）、www.donki.com は、表示のためだけの通信で認証には関係しない。
- 詰まった点:
  - HTMLのフォームを送信する方式ではなく JavaScript(XHR) で認証しているため、汎用のフォーム送信（captive_auth の標準動作）では通らない。
  - 記録が agreement.html から始まっていて、最初のリダイレクトと index.html が記録されていない。sanitized なのでクッキーの有無も分からない。
- 対処: `captive_lib.py` に**ポータル別レシピ**の仕組みを追加し、`wi2-onetap` を実装した（リダイレクトをたどってクッキーを取得 → agreement.html を開く → JSONでPOSTする）。模擬のWi2ポータルで ONLINE になることを確認した。**実機での確認はまだ**。
- 次回の現地確認: ① `captive_probe.py` で「★ 既知のポータル: wi2-onetap」と出るか見る ② `captive_auth.py` で認証できるか試す。失敗したら、DevTools を先に開いた状態で `http://neverssl.com` から記録し直す。

### Step 18: ドン・キホーテで captive_probe を実行（09-29）
- 判定は PORTAL で、**`★ 既知のポータル: wi2-onetap` を検出した**。
- 実際の転送の流れ（hop_0 は本文なしの302なので、空ファイルで正常）:
  1. `http://connectivitycheck.gstatic.com/generate_204` → 302
  2. `https://service.wi2.ne.jp/wi2auth/redirect?cmd=login&mac=<端末MAC>&ip=10.68.x.x&essid=%20&apname=tunnel%201&url=...` → 302。**Set-Cookie: session_id**（端末のMAC/IPとセッションをひも付ける）
  3. `https://service.wi2.ne.jp/freewifi/donki/index.html` → 200（Set-Cookie: AWSALB など）。フォームは `GET agreement.html` の Connect ボタンだけ
- hop_1 の本文には、**WISPr 1.0 の XML** が入っていた（LoginURL `https://service.wi2.ne.jp/wi2auth/wispr/login`）。これはIDとパスワードでログインする有料会員向けの入口と思われ、onetap では使わない。
- 分かったこと: 認証の POST には、2で受け取る `session_id` クッキーが必要だと考えられる → レシピは同じセッションで転送をたどってから POST しているので、この条件を満たしている。
- 端末のMACアドレスは、ゲートウェイが転送URLに付けてくる。ESP32/Pi 側でMACを送る必要はない。
- 不具合: netsh の出力（UTF-8）を CP932 として読んでいたため文字化けし、MACアドレスを取得できなかった → UTF-8 を先に試すよう修正した。
- 次: `captive_auth.py` で実際に認証が通るかを確認する。

### Step 19: ★ドン・キホーテ（Wi2）でブラウザを使わない自動認証に成功（10-06）
- `captive_auth.py` を実行 → `recipe: wi2-onetap` → 結果 **ONLINE**。
  1. generate_204 → 302 → `wi2auth/redirect?cmd=login&mac=…&ip=10.68.92.158…`（Set-Cookie: session_id）→ index.html
  2. `GET agreement.html` → 200
  3. `POST /wi2auth/xhr/login`（JSON onetap agree=1）→ `{"result":true,"licensed":null,"message":null}`
  4. 再判定 → generate_204 が 204（ONLINE）
- 3回の調査（09-29、10-05、10-06）で、APやIPアドレスが変わっても同じ手順で認証できることを確認した。
- 結論: **標準ライブラリだけで、ブラウザなしに認証できる**。Pi Zero（Python）にもESP32（HTTPS POST 1回）にも移植できる。
- 次: wifi_ui.py に captive_lib を組み込み、接続後に PORTAL を検出したら、既知のレシピで自動認証する。

### Step 20: Piへ自動認証を組み込み（10-06）
- `wifi_ui.py` に `captive_lib.py`（pc_tools と共通）を組み込んだ。
  - 接続後、`ensure_online()` で判定する → PORTAL かつ既知のレシピなら自動で認証 → 再判定し、TFT に ONLINE と「auth wi2-onetap: OK」を表示する。
  - **Keeper スレッド**: 60秒ごと（環境変数 `KEEP_INTERVAL` で変更可）に判定し、認証が切れていたら再認証する。起動時から動くので、保存済みのWi-Fiへ自動接続した場合も対応する。画面は定期確認の結果が変わったときだけ描き直す。
  - 未知のポータルでは、フォームの一覧と **TRY** ボタンを表示する（同意型のフォームを1回だけ送信する）。TRY は左端に置かない。
  - ポータルに関するイベントを `captive_logs/portal-YYYYMMDD.jsonl` に記録する。
  - 旧来の probe / fetch_portal / save_log は削除した。
- deploy: `deploy.ps1` が `pc_tools\captive_lib.py` も送り、`pi_install.sh` が /opt/sipphone に設置する。
- 模擬環境で確認: Wi2型 → 自動で ONLINE。汎用フォーム型 → PORTAL 表示 → TRY → ONLINE。

### Step 21: 現地試験に向けた準備（10-09）
- **TFTなしでの予備動作（headless）を追加**: 328P が応答しない場合は、画面を使わずに定期確認と自動認証だけを続ける。状態が変わったときだけ `captive_logs/headless.log` に記録する。模擬Wi2で ONLINE になることを確認した。
- 事前に、ドン・キホーテ用のプロファイル（open・MAC固定）を作っておけば、店内では電源を入れるだけで自動接続 → 自動認証まで進む。
- 注意: 前回の netsh の記録では、PCは **5GHz**（ch100/128）につながっていた。Pi Zero W / 2W は2.4GHzにしか対応していないので、店内で同じSSIDの2.4GHzが出ていなければ接続できない（TFTの一覧に DONKI_Free_Wi-Fi が出るかで判断できる）。

### Step 22: SIPサーバー（Pi Zero W）の準備ガイドを作成（10-16）
- 途中の不具合の記録:
  - TFT基板に、温度表示機のスケッチ（ADT7410_LCD_AAA）を書き込んだままの 328P を挿していた → tft_coproc を書き直し、ヒューズも外付け16MHzに変更した。
  - TXとRXの配線違い → 配線し直して復旧した。
  - 起動直後に勝手に一番上の行が選ばれる（タッチの誤検出）。触れていなくても x≈370、y≈666 と、z が負の大きな値が出続けた → はんだブリッジ（D6/D7、A1/A2）か、パネルが物理的に押されていることを疑った。その後ユーザーが対処し、動作するようになった。
- `server/` を作成: SERVER_GUIDE.md（7ステップ）、kamailio.cfg（最小構成）、wait-tailscale.conf、install_kamailio.sh。
  - Kamailio は登録と呼び出しの中継だけ。登録情報はメモリのみ。Tailscale網（100.64.0.0/10、fd7a:115c:a1e0::/48）以外は 403。RTPは中継しない。メモリ割り当ては 32MB/4MB。
  - クラウド環境の Kamailio 5.7 で確認: `kamailio -c` 合格、REGISTER 200、INVITE 転送（Record-Route 付き）、未登録宛 404、Tailscale網以外は 403、`kamcmd ul.dump` が使える。
- microSD は16GBで十分（使用量は3〜4GB）。16MBでは不可。

### Step 23: 新しいSDで端末を再構築、タッチの「最初の1回だけずれる」問題（10-26）
- SDを作り直した（Zero W、192.168.1.4）。`--cal` を実行すると `TFT no ACK: M 1` → 画面なしの動作（headless）になった。原因は wifi-ui サービスとの二重起動。
  - 対処: wifi_ui.py に二重起動チェックを追加（自分と、sudo などの親プロセスは除外）。`M 1` に応答がなければ最大3回やり直す。
- 症状: 校正テストで**左の十字の1回目のタップだけ、大きく右下にずれ**、2回目は正常。中央と右は正常。校正後、CALボタンを押すと右隣の OFF が反応する。
- 推定原因: 測定の切り替え直後に、電圧が落ち着く前の値を読んでいる。Adafruit TouchScreen は AVR では待ち時間なしでAD変換する。膜の抵抗が高い左側と、描画した直後（LCDとタッチで線を共用）の最初の測定で、ずれが大きくなる。
- 対処（328P、要書き込み）: タッチの読み取りを自前の `readTouch()` に置き換えた。
  - 計算式と値の向きは Adafruit と同じなので、校正値はそのまま使える。
  - 端子を切り替えたあと150µs待ち、1回目の読み取りは捨て、2回の平均を取る。
  - 押下と確定する条件: 押し始めの1回を捨て、続く3回の値のばらつきが±12以内になったら、その平均を送る。z が 200〜20000 の範囲外なら無効。
  - TouchScreen ライブラリは不要になった。コンパイル結果は 20838 bytes。
- UI: CALボタンを左へ移動し、OFFとの間を38px空けた。

### Step 24: SIPサーバー（sipserver, <SERVER_TS_IP>）が起動した（10-29〜30）
- 詰まった点1: サーバー用に使ったSDでは、本体の **Wi-Fi が認識されない**（`ip link` に wlan0 がない。`dmesg` に `mmc1: Failed to initialize a non-removable card`）。同じSDを別の Pi Zero に挿すと Wi-Fi は動いた → **その本体の Wi-Fi 回路の故障**と判断し、使用を保留した（USB-LAN を付ければ有線サーバーとして使える可能性がある）。Wi-Fi が動く個体でサーバーを構築した。
- 詰まった点2: 初回起動は cloud-init の処理で約8分かかる。その途中の完了メッセージが、ログイン入力欄の後ろに表示されるため、止まっているように見える。
- 詰まった点3: Raspbian（armhf）版の **Kamailio 6.0.1** が `Too much qm pkg memory demanded`（-M 4 でも出る）で起動しない。
  - ソースを確認した結果、メモリ管理方式 qm の初期化時の検査（16バイト単位の整列チェック）に失敗していると推定した。
  - 対処: **TLSF に切り替える**（`-X tlsf -x tlsf`）。systemd の追加設定 `memmgr.conf` で ExecStart を上書きする。
- 結果: `active`。UDP <SERVER_TS_IP>:5060 と 127.0.0.1:5060 で待ち受けている（プロセス数は11）。
- 次: MicroSIP（1001）と AGEphone（1002）で、登録と通話を試す。

### Step 25: ★PC(MicroSIP 1001) ⇔ iPhone(AGEphone 1002) の通話が成立した（10-30）
- PC の Tailscale は、数秒で切断を繰り返していた → 1.104.4 に更新し、トレイアプリを起動した状態で接続すると安定した（`--unattended` でも可）。
- AGEphone にはポートの入力欄がない → 既定の 5060 のままで問題なかった。
- sngrep で確認: REGISTER と INVITE が両方向とも Tailscale の IP（iPhone <IPHONE_TS_IP>、PC <PC_TS_IP>）から届き、通話が成立した。
- MicroSIP が Contact に自宅LANのIP（192.168.1.13）を書いてくる → kamailio.cfg に nathelper を追加した（fix_nated_register / fix_nated_contact / fix_nated_sdp で、実際の送信元に書き換える）。クラウド環境で動作を確認した。
- 残り: Pi の通話端末（Tailscale、音声機器、baresip、TFTの通話画面、起動の順序）と、ドン・キホーテでの現地試験。

### Step 26: 端末Pi（sipphone）の音声と baresip、TFT の通話画面（10-30）
- ヘッドセット: USB-C のマイク付きイヤホン（ANGV-EPC10-A）を、OTG 変換アダプタ経由で内側の USB ポートに接続した。DAC 入りなので USB Audio として認識された（card 0）。
  - 起動のたびにカード番号が変わることがあるため、番号ではなく名前で指定する（`plughw:ANGVEPC10A,0`）。
  - 音量は `amixer -c ANGVEPC10A sset PCM 100%` / `Mic 90%` で調整し、`alsactl store` で保存した。
  - dmesg の `Unlikely big volume range` は、安価なUSB音声機器でよく出る警告で、無害。
  - HID（input0）も出ていて、ボタンをキー入力として読める。
  - 注意: alsamixer の初期表示は vc4-hdmi（HDMIの音声）。SSH 経由で F6 が効かないときは、`S` キーか `-c` オプションでカードを選ぶ。
- Tailscale と baresip（1003、PCMU、`net_interface tailscale0`）を入れて、手動起動での通話に成功した。
- TFT の通話画面を作った（`wifi_ui.py` に統合。baresip の操作は `phone.py`）。
  - 操作方法: baresip の ctrl_tcp（127.0.0.1:4444、netstring JSON）経由で行う。baresip は systemd で常駐させる（`baresip.service`、stdio は無効）。
  - 画面構成:
    - ホーム: SIP 登録状態、Wi-Fi、Tailscale の IP、短縮ダイヤル（`contacts.json`、最大4件）、DIAL、HIST、WIFI、CAL、OFF。
    - ダイヤルパッド。
    - 着信履歴（`call_log.json`、in / out / miss）。
    - 通話画面: 着信時は応答・拒否、通話中は音量・ミュート・切断。
  - 着信時の割り込み: `Screen.wait` の待機中に着信を確認し、どの画面からでも通話画面を出す。戻るときは、元の画面を描き直させる。
  - 着信音・呼出音は鳴らさない（`audio_alert alsa,null`）。
  - ヘッドセットのボタン: 再生/一時停止などのキーで、応答・切断を兼用する。ほかにミュートと音量のキーにも対応した。TFT がなくても（headless でも）使える。
  - キャプティブポータルの再認証などで ONLINE に戻ったときは、`uareg` で SIP の登録をやり直す。
- 詰まった点（クラウドでの確認時）: `threading.Thread` を継承したクラスに `_handle` という名前のメソッドを作ると、Python 3.13 では内部の属性と衝突して受信スレッドが落ちる → メソッド名を変更した。
- クラウドで、疑似 328P（pty）と疑似 baresip を使って、次の流れを確認した: 発信 → 呼出 → 通話 → ミュート → 切断、着信 → 応答 → 相手側の切断、不在着信、ダイヤルパッド、履歴。

### Step 27: baresip の常駐化と、iPhone が登録されない問題（10-30〜11-02）
- 詰まった点1: TFT に `SIP: baresip not running` と表示された → `deploy.ps1` を **`-Phone` を付けずに**実行していた（2回）。このため `baresip.service` が作られず、ctrl_tcp も有効になっていなかった。
  - 見分け方: `-Phone` 付きで実行すると、最後に `---- config (要点) ----`、`---- accounts ----`、`---- service ----` が表示される。
- 詰まった点2: 新しく作った Debian 版 baresip の config には、`ctrl_tcp_listen 0.0.0.0:4444` の行が最初からある。ただし `module_app ctrl_tcp.so` が無いため、操作口は開かない。`baresip_setup.sh` は両方を設定し、待ち受けを 127.0.0.1 に限定する。
- 詰まった点3: PowerShell で `Z:\sipphone\deploy.ps1` を実行して「認識されません」 → こちらが例として書いたパスをそのまま使っていた。正しくは `G:\sipphone\deploy.ps1 -Phone`。
- 詰まった点4: 端末Pi（sipphone）で `sngrep` を実行して `command not found` → sngrep はサーバーPi（sipserver）にしか入れていない。**どのPiで実行するコマンドか**を確認する。
- 詰まった点5: iPhone（AGEphone 1002）が「時々しか登録できない」。
  - 症状: Tailscale の管理画面では接続中と表示されるのに、`kamcmd ul.dump` に 1002 が無く、sngrep にも REGISTER が来ない。
  - 対処: iPhone の Tailscale アプリを**一度オフにしてからオン**にし、すぐ AGEphone を開くと登録できた。
  - 背景: iOS は、バックグラウンドで SIP アプリが通信を続けることを許さない。iPhone の Tailscale も待機中は止まることがある。自前サーバー（Tailscale 内）では Push 着信が使えないため、**iPhone は「アプリを前面に出している間だけ登録・着信できる」**のが前提になる。
- 未反映: `ul.dump` で 1001 の Address が `192.168.1.37`（PCの自宅LANのIP）のまま。nathelper 版の kamailio.cfg をサーバーに入れ直す（`install_kamailio.sh` の再実行）必要がある。入れ直すまでは、自宅外から PC にかけても声が届かない可能性がある。

---

## 確認チェックリスト（詰まったときに上から見る）

### 電源・配線・ファームウェア
- [ ] Pi の 5V → UNO の **5V ピン**（Vin ではない）。GND も共通にする。
- [ ] Pi のピン8(TX) → D0、D1 → **分圧（2.2k/3.3k）**→ Pi のピン10(RX)。TX と RX の取り違えにも注意。
- [ ] 328P のファームウェアが `tft_coproc` になっているか。シリアルモニタで `READY 9488` が出るかで確認する。別のスケッチ（ADT7410など）が入っていると、まったく応答しない。
- [ ] 書き込み設定: MiniCore / ATmega328 / **External 16MHz** / 書込装置を使って書き込む（ISP）。ボードの選択が ESP32 などのままだと、`GPIO` のエラーが出る。
- [ ] Arduino のスケッチを OneDrive 内の日本語パスに置かない（grpc の UTF-8 エラーの原因になる）。

### Pi の基本
- [ ] Pi Zero W は **2.4GHz のみ**（Buffalo なら SSID 末尾が -G の方）。
- [ ] 初回起動は cloud-init で約8分かかる。止まって見えても、待つ。
- [ ] `ip link` に wlan0 が無く、dmesg に `mmc1: Failed to initialize` → 本体の Wi-Fi 回路の故障を疑う（SDではない）。
- [ ] UART: `ls -l /dev/serial0` が `ttyAMA0` を指しているか。`dtoverlay=disable-bt` と、シリアルコンソールの無効化（`deploy -Setup`）。
- [ ] `TFT no ACK: M 1` → wifi_ui.py が2つ動いていないか（`pgrep -af wifi_ui`）。手動で起動する前に `sudo systemctl stop wifi-ui` を実行する。

### タッチ
- [ ] ずれるときは、ホーム画面の CAL で9点を校正する（`sudo python3 /opt/sipphone/wifi_ui.py --cal` でも可）。
- [ ] 膜の状態は `touch_check.py` で調べる（wifi-ui を止めてから実行する）。
- [ ] 何も触れていないのに押される → D6/D7、A1/A2 のはんだブリッジや、パネルへの圧迫を疑う。

### Wi-Fi・キャプティブポータル
- [ ] 接続できない → 専用プロファイル `sip-<SSID>` を作り直す（WPA2/3 混在の AP は wpa-psk を明示する）。
- [ ] MAC アドレスはランダム化しない（`cloned-mac-address permanent`）。ランダム化すると、ポータルの認証が保たれない。
- [ ] PORTAL のまま → `captive_logs/` を `deploy -Logs` で取得し、PC の `captive_probe.py` / `captive_auth.py` で解析する。
- [ ] HAR ファイルには Cookie が入る。**共有するのは `har_summary.py` の出力だけ**にする。

### Tailscale
- [ ] すべての機器を同じアカウントでログインし、管理画面で **Disable key expiry** にする。
- [ ] Windows: トレイアプリが起動していないと、数秒で切断される（`--unattended` でも可）。
- [ ] iPhone: 「接続中」と表示されていても通信できないことがある。その場合は**一度オフにしてからオン**にする。

### SIPサーバー（Kamailio）
- [ ] 32bit Raspbian の Kamailio 6.0 は `-X tlsf -x tlsf` が必須（`memmgr.conf`）。
- [ ] 待ち受けの確認: `ss -lunp | grep 5060` で、Tailscale の IP と 127.0.0.1 の両方が出るか。
- [ ] 登録状況: `sudo kamcmd ul.dump`。Address が **100.x（Tailscale）**になっているか（192.168.x なら nathelper の設定が未反映）。
- [ ] 通信の確認: `sudo sngrep port 5060`（**サーバー側で**実行する）。REGISTER が来ない → 送信元の Tailscale の問題。403 → Tailscale 以外の経路から来ている。
- [ ] サーバーの欄には Tailscale の IP を入れる（LAN の IP では 403 になる）。

### 端末（baresip・音声）
- [ ] ヘッドセットは DAC 入り（USB Audio）か。`aplay -l` に表示されるか確認する。変換アダプタは OTG 対応のものにし、Pi の内側の「USB」ポートに挿す。
- [ ] カードは名前で指定する（`plughw:ANGVEPC10A,0`）。alsamixer の初期表示は HDMI なので、F6 か `S` でカードを切り替える。
- [ ] `systemctl status baresip` が active か。`ss -ltnp | grep 4444` で操作口が開いているか。
- [ ] 手動で起動した baresip が残っていると、ヘッドセットを取り合う → `pkill -x baresip`。
- [ ] ヘッドセットのボタン: `sudo journalctl -u wifi-ui -f | grep phone` で key code を確認する。
- [ ] 設定を変えたら `deploy.ps1 -Phone` を実行する（`-Phone` を付けないと baresip の設定は変わらない）。

### クライアント
- [ ] MicroSIP: ドメイン = サーバーの Tailscale IP。コーデックは G.711 を優先し、STUN / ICE はオフにする。
- [ ] AGEphone: ポートの欄は無い（5060 のままでよい）。前面にある間だけ登録される前提で使う。

---

## 未解決・今後のTODO
- [x] UNO → Pi の受信不通を解消する（Step 9）
- [x] 電源を 5V ピンからの給電に変更する
- [x] 9点メッシュでタッチを校正する（Step 12）
- [x] wifi_ui で AP 一覧の表示と接続を実機確認する（Step 16）
- [x] キャプティブポータルの自動認証（Wi2 onetap）（Step 19〜20）
- [x] SIPサーバーの稼働、PC ⇔ iPhone の通話（Step 24〜25）
- [x] 端末の baresip、TFT の通話画面、ヘッドセットのボタン（Step 26〜27）
- [ ] サーバーに nathelper 版の kamailio.cfg を反映する（1001 の Address が 100.x になることを確認する）
- [ ] 待ち時間を入れた新しい tft_coproc.ino を書き込み、最初の1回だけタッチがずれる問題が直ったか確認する
- [ ] ドン・キホーテでの現地試験（端末 ⇔ モバイル回線の iPhone / 自宅の PC）
- [ ] 再起動後に自動で復帰するか確認する（Wi-Fi → 認証 → Tailscale → baresip の順）
- [ ] （任意）SIP のパスワード認証（digest）、iPhone の着信対策、ロータリーエンコーダでの操作、USB-LAN で故障した Zero W を再利用する
- [ ] 328P-PU をユニバーサル基板に移設する
