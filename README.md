# sipphone — 初代 Pi Zero W で作る SIP 電話端末とサーバー

性能の低いボードでどこまでできるかを試すプロジェクトです。

- **端末**: Raspberry Pi Zero W + 3.95" ILI9488 TFT（Arduino UNO 用シールド）+ USB ヘッドセット。TFT は ATmega328P を表示コプロセッサにして UART で動かす。
- **サーバー**: もう1台の Pi Zero W で Kamailio（登録と中継だけ）。
- **回線**: キャプティブポータルのあるフリー Wi-Fi に自動でログインし、Tailscale 経由で無料通話する。
- **子機**: PC（MicroSIP）やスマホ（AGEphone など）も内線としてつながる。

```
[端末 Pi Zero W]──UART 250000bps──[ATmega328P]──[ILI9488 TFT + 抵抗膜タッチ]
  wifi_ui.py / phone.py / baresip(1003)
        │ フリー Wi-Fi（ポータル自動認証）
        ▼
   ── Tailscale (100.64.0.0/10) ──┬── [PC MicroSIP 1001]
        │                         └── [スマホ AGEphone 1002]
[SIPサーバー Pi Zero W] Kamailio 6.0（REGISTER / INVITE の中継のみ。音声は端末同士で直接）
```

## できること
- TFT で Wi-Fi を選ぶ（携帯電話式のマルチタップでパスワードを入力）
- キャプティブポータルを検出し、既知のポータル（Wi2 の onetap 方式）ならブラウザなしで自動認証する。60秒ごとに状態を確認し、必要なら再認証する
- ホーム画面: SIP の登録状態、Wi-Fi、Tailscale の IP、短縮ダイヤル、ダイヤルパッド、通話履歴
- 着信時はどの画面からでも通話画面に切り替わる（応答 / 拒否 / 音量 / ミュート / 切断）
- USB ヘッドセットのボタンで応答・切断・音量を操作する（TFT がなくても使える）
- 抵抗膜タッチの非線形を9点メッシュで補正する（校正画面あり）

## ファイル構成
| パス | 内容 |
|---|---|
| `wifi_ui.py` | 端末のメイン（TFT UI、Wi-Fi、ポータル認証、通話画面）。systemd の `wifi-ui.service` で動かす |
| `phone.py` | baresip の操作（ctrl_tcp）、ヘッドセットのボタン、短縮ダイヤル / 履歴 |
| `baresip_setup.sh`, `baresip.service` | baresip の設定と常駐化 |
| `touch_check.py` | タッチパネル（抵抗膜）の健全性チェック（端末 Pi で実行） |
| `touch_diag/touch_diag.ino` | タッチパネル単体の故障診断（UNO に書き込み、シリアルモニタ 115200bps。膜の断線・短絡・押下を判定） |
| `pi_install.sh`, `deploy.ps1`, `deploy.bat` | Windows PC から端末 Pi へ転送・インストール |
| `tft_coproc/tft_coproc.ino` | ATmega328P 用ファームウェア（描画とタッチ読み取り、行コマンド方式） |
| `pc_tools/` | Windows PC 用のポータル解析・認証ツール（`captive_lib.py` は端末と共通） |
| `server/` | SIPサーバー（Kamailio）の設定とインストールスクリプト、構築ガイド |
| `docs/` | ハードウェアの配線、回路図（`schematic.png` / `.svg`）、実現性検討、開発ログ（詰まった点とチェックリスト） |
| `tools/` | `tft_render.py`（実機なしで TFT 画面を PNG 化）、`schematic.py`（回路図の生成） |

## 必要なもの
- Raspberry Pi Zero W ×2（端末用とサーバー用。端末は Zero 2 W でも可）、microSD 16GB 以上
- 3.95" ILI9488 8bit パラレル TFT シールド（UNO 形状、抵抗膜タッチ）
- ATmega328P（UNO、または UNO 互換の自作基板）、分圧抵抗 2.2kΩ / 3.3kΩ
- USB ヘッドセット（**DAC 入り = USB Audio クラス**）と OTG 変換アダプタ
- Tailscale アカウント（無料）

## セットアップの流れ
1. **配線と 328P の書き込み**: [docs/hardware-setup.md](docs/hardware-setup.md)、回路図 [docs/schematic.png](docs/schematic.png)
   - MiniCore / ATmega328 / External 16MHz で書き込む。
2. **SIPサーバー**: [server/SERVER_GUIDE.md](server/SERVER_GUIDE.md)
   - Raspberry Pi OS Lite 32bit → Tailscale → `install_kamailio.sh`。
3. **端末**:
   1. Raspberry Pi OS Lite 32bit を入れ、`deploy.ps1` の `$PiHost` / `$PiUser` を自分の環境に合わせる。
   2. `deploy.bat -Key`（SSH 鍵の登録）→ `deploy.bat -Setup`（UART と自動起動）。
   3. 端末に Tailscale を入れ、`sudo tailscale up`。
   4. `sudo apt install -y baresip-core alsa-utils`。
   5. `deploy.bat -Phone -Ext 1003 -SipServer <サーバーの Tailscale IP>`。
4. **クライアント**: MicroSIP / AGEphone の「サーバー」に、サーバーの Tailscale IP を設定する（ユーザー名 = 内線番号）。
5. 短縮ダイヤルは、端末の `/opt/sipphone/contacts.json` を編集する。

## 制限と注意
- **認証なし**: SIP のパスワード認証（digest）はまだ実装していません。Tailscale の中（自分の機器だけ）で使う前提です。サーバーを公開ネットワークに出さないでください。
- **iPhone**: iOS はバックグラウンドの SIP 通信を許さないため、アプリを前面に出している間だけ登録・着信できます。無料版 AGEphone のプッシュ着信は対応サーバーが限られ、自前の Kamailio では使えません。
- **サーバー再起動後の再登録**: Kamailio の登録情報はメモリのみです。再起動後に MicroSIP が再登録しない場合は、トレイから「終了」→ 起動します（`sudo kamcmd ul.dump` で確認）。
- **タッチがほとんど反応しない**: 生値がずれて校正が合わなくなっていることがあります。`sudo python3 /opt/sipphone/wifi_ui.py --cal` で校正画面から起動してやり直します。
- **ポータルの自動認証**: 対応しているのは Wi2 の onetap 方式だけです。利用規約への同意を自動化するものなので、各サービスの規約を確認したうえで、自己責任で使ってください。
- **一度に1通話だけ**: 通話中の2本目の着信は無視します。着信音・呼出音は鳴らしません（画面表示のみ）。
- **Kamailio 6.0 の起動オプション**: 32bit Raspbian では `-X tlsf -x tlsf` が必須です（`server/memmgr.conf`）。
- **ログの扱い**: `captive_logs/`、HAR ファイルには Cookie や MAC アドレスが入ります。リポジトリには入れないでください（`.gitignore` 済み）。

詰まったときは [docs/devlog.md](docs/devlog.md) の「確認チェックリスト」を参照してください。

## ライセンス
MIT License（[LICENSE](LICENSE)）
