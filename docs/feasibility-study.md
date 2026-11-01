# SIP電話システム 実現性検討

更新: 2026-09-25

## 決定事項・進捗
- 手持ち: Pi Zero W v1.1 ×1, Pi Zero 2 (W) v1.1 ×1, Pi 4
- TFT: reg(0xD3)=0x9488 → ILI9488 確定（8bit parallel UNOシールド）
- 表示方式: 案C採用。ATmega328P-PU をUNO用ユニバーサル基板に載せ、表示/タッチのコプロセッサにする。Pi とは UART 250000bps、行コマンド+ACK方式
- Step1 実装済み (tft_coproc.ino / wifi_ui.py):
  - AP一覧→選択→(鍵付きはTFTキーボード)→接続
  - generate_204 で ONLINE/PORTAL/NO_NET を判定
  - ポータルのHTMLとフォーム構造を captive_logs に保存
- 次: ショッピングセンターのフリーWi-Fiで現地試験 → ログをもとに自動認証を実装

## 結論
- 端末・サーバーとも初代Pi Zero (W) で成立の見込みあり。条件は以下の4つ:
  - G.711固定、トランスコードなし
  - エコーキャンセラなし（受話器/ヘッドセット運用）
  - 表示は部分更新
- Tailscale でNATが消えるため、サーバーはシグナリング専用になる（RTPは端末間で直通）→ Pi Zeroで足りる。
- 最大の難所:
  1. TFT駆動+抵抗膜タッチ → 案Cで解決
  2. 音声I/O（Pi Zeroは音声入出力なし）
  3. キャプティブポータル認証

## 構成
- [Pi Zero端末: baresip + tailscale] ~~Wi-Fi(captive)~~ Tailscale網 ~~ [自宅 Pi Zero: Kamailio(registrar) + tailscale]
- [スマホ: AGEphone + Tailscaleアプリ] も同じTailscale網から登録する。
- 音声は 100.x.x.x 同士で直接RTP。

## 端末
### 表示
- 案A: fbtft パラレルGPIO（遅い）
- 案B: BCM2835 SMI（DMA。ギリギリを攻める枠）
- 案C: 328Pコプロセッサ（採用）
### 音声
- USBオーディオ(CM108等) または I2S(MAX98357A + INMP441)。
- 受話器/ヘッドセット前提（AECを回避するため）。
### ソフト
- baresip + G.711
- Wi-Fi省電力OFF（iw wlan0 set power_save off）
- Raspberry Pi OS 32bit Lite（ARMv6対応。Debian armhfはARMv7向けなので不可）

## サーバー
- 推奨: Kamailio（registrarのみ。rtpproxy不要）。Raspberry Pi OS のパッケージで入る。
- Asterisk: Debian系に収録されていないためソースビルドが必要。Pi Zero上では長時間かかるので、Pi 4でビルドする。
- 留守電/IVR/外線を使いたいなら Pi 4 + Asterisk。
- Tailscale IPは固定なので、sip:ext@100.x.x.x への直接発信でも通話できる。

## Tailscale
- ARMv6対応パッケージあり。G.711なら負荷は小さい。
- UDPが遮断されていても DERP(TCP443)経由でつながる（遅延は増える）。
- 無料で話せるのは網内の内線同士のみ。公衆網への発着信にはSIPトランク（有料）が必要。

## キャプティブポータル
- 案1: AP+STA同時動作。スマホをPiのAPにつないで認証すると、PiのMACが認証される。
- 案2: ポータルごとの自動認証。Guest2-Repeater の方式を参考にする。
- 案3: MACクローン（利用規約に抵触するおそれ）。

### Guest2-Repeater（コマンドライン版 2026.01.27）評価
- 概要: 5分周期で以下を行う常駐ツール。
  1. TCPで 8.8.8.8:53 への接続を確認
  2. HTTP generate_204 を確認（リダイレクトは追わない）
  3. NGなら headless Chromium（Selenium）で規約同意ボタン id="ui_aup_accept_button" をクリック
  - 毎周期、処理は子プロセスで実行して切り離す。
- ボタンID `ui_aup_accept_button` から見て、対象ポータルは Cisco ISE のHotspot（AUP同意型）と判断。
- 本プロジェクトへの流用:
  - 2段階チェックと generate_204 のリダイレクト非追従 → wifi_ui.py と同じ方式で、そのまま使える。
  - 「定期確認→NGなら再認証」の常駐ループ構造 → 流用する。
- 本プロジェクトで使えない/変える点:
  - Chromium+Selenium は Pi Zero W (ARMv6) では動かない。Pi Zero 2 W でもRAM 512MBでは厳しい。
    → HTMLを解析して requests でフォームを再送信する方式に置き換える（ポータルごとのレシピ方式）。
  - 8.8.8.8:53 のNGを「物理断」とみなして rfkill でリセットしている。未認証の状態では外部DNSが遮断されるポータルが多いため、認証前に毎回無駄なリセットが入る。認証後も外部DNSを塞ぐ網では、毎周期リセットされる。
    → 物理断の判定はAPへの関連付け状態（nmcli/iw）とゲートウェイへの疎通で行う。
  - 5分周期では、切れてから最大5分間電話がつながらない。HTTPチェック自体は軽いので30〜60秒周期にし、重い認証処理はNG時だけ行う。
  - 同意ボタンIDは固定値で、INT_guest2 専用。

## AGEphone
- iOSではバックグラウンド着信が不安定になる可能性がある。実機で確認が必要。

## 未確認事項
- 通話スタイル（受話器 or ハンズフリー）
- スマホがiOS/Androidのどちらか
- 端末/サーバーにどちらのPiを割り当てるか
