# Step 1: TFT で Wi-Fi 選択・接続・キャプティブ検出

```
[Pi Zero W] --UART 250000bps--> [ATmega328P (UNO互換, 5V/16MHz)] --UNOヘッダ--> [3.95" ILI9488 シールド]
  wifi_ui.py (nmcli)               tft_coproc.ino (描画/タッチ)
```

## 1. 328P 基板 (UNO用ユニバーサル基板)

| UNO端子 | 328P-PU ピン | 用途 |
|---|---|---|
| D0 (RX) | 2 (PD0) | ← Pi TXD |
| D1 (TX) | 3 (PD1) | → 分圧 → Pi RXD |
| D2–D7 | 4,5,6,11,12,13 | LCD D2–D7 |
| D8, D9 | 14, 15 | LCD D0, D1 |
| D10–D13 | 16–19 | シールドのSD(未使用) |
| A0–A4 | 23–27 | LCD RD/WR/RS/CS/RST (A1–A3はタッチ共用) |
| A5 | 28 | 未使用 |
| 5V | 7 (VCC), 20 (AVCC) | 各100nFでGNDへ |
| GND | 8, 22 | |

- 16MHz 水晶を 9–10ピンに接続（各ピンから22pFでGNDへ）。RESET(1ピン)は10kでプルアップ。AREF(21ピン)は100nFでGNDへ。
- シールドの 3V3 端子は、シールド側の回路につながっているかテスターで確認する。シールド側でLDOを持っていれば3V3端子は空きでよい。

## 2. Pi Zero W との配線

| Pi (物理ピン) | 接続先 |
|---|---|
| 8 GPIO14 TXD | 328P 2ピン (RXD)。直結可（3.3Vは5V動作時のHigh判定3.0V以上） |
| 10 GPIO15 RXD | 328P 3ピン(TXD) —2.2kΩ— ●(→Pi RXD) —3.3kΩ— GND  (約3.0Vに分圧) |
| 2 / 4 5V | 基板の5V（Piの電源は2A以上推奨） |
| 6 GND | GND |

## 3. 328P への書き込み

1. **タッチ校正**：UNO＋シールドで `MCUFRIEND_kbv → TouchScreen_Calibr_native` を実行する。シリアルモニタに出る "COPY-PASTE" の2行を `tft_coproc.ino` の該当箇所へ上書きする。
2. `tft_coproc.ino` を書き込む。328P-PU にブートローダが無い場合は、「Arduino as ISP」でブートローダを書くか、UNO のソケットに挿して書き込んでから基板へ移す。
3. 単体確認：UNO でシリアルモニタを 250000bps・改行LF に設定し、次を送る。
   - `C 0` を送ると画面が黒くなる。
   - `T 10 10 2 65535 -1 Hello` を送ると文字が出る。
   - 画面に触れると `D x y` が返る。

## 4. Pi 側セットアップ（Raspberry Pi OS Lite, Bookworm以降）

```sh
sudo raspi-config nonint do_serial_cons 1     # シリアルコンソール無効
sudo raspi-config nonint do_serial_hw 0       # UART有効
echo "dtoverlay=disable-bt" | sudo tee -a /boot/firmware/config.txt   # PL011をGPIOへ（Bluetooth無効）
sudo systemctl disable hciuart
sudo apt install -y python3-serial
sudo mkdir -p /opt/sipphone && sudo cp wifi_ui.py /opt/sipphone/
sudo cp wifi-ui.service /etc/systemd/system/ && sudo systemctl enable wifi-ui
sudo reboot
```
手動で試す場合は `sudo systemctl stop wifi-ui; sudo python3 /opt/sipphone/wifi_ui.py` を実行する。

## 5. 操作
- 一覧：信号の強い順に表示され、接続中は緑、OPENは黄で示す。`< SCAN >` でページ送りと再スキャン、`OFF` で安全にシャットダウンする。
- 鍵付きのネットワークはキーボードで入力する（Aa=大文字、#+=記号）。
- 接続後に `generate_204` で疎通を判定する：
  - **ONLINE**：インターネットに出られる。
  - **PORTAL**：認証が必要。リダイレクト先のポータルページを取得し、フォーム構造を表示する。
  - **NO_NET**：DNSの失敗など。
- 判定のたびに `/opt/sipphone/captive_logs/` へ保存する：
  - 日時_SSID.json：IP・MAC・HTTP応答・フォームの action と input
  - *_probe.html / *_portal.html
  
  次のステップ（自動認証）はこのログをもとに作る。

## 現地テスト時の注意
- Piには時計（RTC）が無いため、時刻がずれていると証明書の検証に失敗する。記録目的なので検証なしで取り直す（cert_ok=False と表示される）。
- ポータルはMACアドレスで認証状態を管理するものが多い。MACはログに記録している。
