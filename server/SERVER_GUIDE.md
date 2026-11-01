# SIPサーバー構築ガイド（Pi Zero W + Kamailio + Tailscale）

1ステップは15〜30分程度。各ステップの最後にある「確認」が通れば完了で、途中で中断しても次はそこから再開できる。
チェック欄 `[ ]` は、終わったら `[x]` にする。

```
[端末 Pi (TFT)]──┐                         ┌── [スマホ AGEphone]
                  ├── Tailscale網 (100.x.x.x) ─┤
[PC MicroSIP]  ──┘        │                 └── …
                    [SIPサーバー Pi Zero W]
                    Kamailio: 登録と呼び出しの中継だけ
                    音声(RTP)は端末同士が直接やり取りする
```

---

## 0. 準備物と前提の確認

| 項目 | 内容 |
|---|---|
| 本体 | Pi Zero W v1.1（1コア/512MB/2.4GHzのみ） |
| microSD | **16GB で十分**（下の容量見積もり参照） |
| 電源 | 5V 1A 以上の micro USB（PWR IN 側） |
| PC | Raspberry Pi Imager、SSH（PowerShell の ssh） |

### 起動ディスクの容量
- **16GB（ギガバイト）なら余裕がある。** 使うのは 3〜4GB 程度。

  | 内訳 | 容量 |
  |---|---|
  | Raspberry Pi OS Lite（32bit） | 約2.5〜3GB |
  | Kamailio | 約20MB |
  | Tailscale | 約60MB |
  | ログ・更新用の余裕 | 1GB |

- **16MB（メガバイト）では足りない。** OSの圧縮イメージだけで約500MBある。書いたのが 16GB の意味なら問題ない。
- 書き込み回数を減らすため、ログは小さめに保つ（ステップ6）。SDの寿命対策として、容量は大きい方が有利（空き領域が多いほど書き込みが分散される）。

### どのPiをサーバーにするか
- いま端末として使っているのは **sipphone（Pi Zero W, armv6l）**。
- Zero W をサーバーにする場合は、端末側を **Pi Zero 2 W** に移すことになる。端末の方がTFT描画とSIP通話で処理が重いので、Zero 2 W に任せるのは理にかなっている。
- このガイドは Zero W・Zero 2 W のどちらでもそのまま使える（32bit版のOSを使う）。

---

## 1. SDカードの作成（PC作業・15分） `[ ]`

1. Raspberry Pi Imager → デバイス「Raspberry Pi Zero」→ OS「**Raspberry Pi OS Lite (32-bit)**」
2. 「設定を編集する」で次を設定する。

   | 項目 | 値 |
   |---|---|
   | ホスト名 | `sipserver` |
   | ユーザー名・パスワード | 端末側（pi）と同じでも可 |
   | Wi-Fi | 自宅の **2.4GHz の SSID**（末尾が -G の方） |
   | ロケール | タイムゾーン Asia/Tokyo、キーボード jp |
   | SSH | 有効（パスワード認証。後で鍵に切り替える） |

3. 書き込み → Piに挿して電源ON → **初回起動は5〜10分ほど放置する**（初回の展開処理が遅い）。

**確認:** PowerShell で `ssh <ユーザー名>@sipserver.local` が通る（つながらない場合は、ルーターの管理画面でIPアドレスを調べる）。

---

## 2. 基本設定（15分） `[ ]`

```sh
sudo apt update && sudo apt full-upgrade -y        # Zero W だと20〜40分かかることがある
sudo raspi-config nonint do_wifi_country JP
# Wi-Fi の省電力をオフ(SIPの着信が遅れる・取りこぼす原因になる)
sudo nmcli connection modify "$(nmcli -t -f NAME connection show --active | head -n1)" 802-11-wireless.powersave 2
# 使わない機能を止めてメモリとCPUを空ける
sudo systemctl disable --now bluetooth hciuart 2>/dev/null || true
free -h; df -h /
```

**確認:** `free -h` で available が 300MB 以上、`df -h /` で空きが 10GB 以上。

（任意）PCから鍵でログインできるようにする場合は、端末用の `deploy.ps1 -Key` と同じ手順で行う。

---

## 3. Tailscale の導入（15分） `[ ]`

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
- 表示されたURLをPCのブラウザで開き、端末Piと**同じアカウント**でログインする。
- `tailscale ip -4` → `100.x.x.x` をメモする（**これがSIPサーバーのアドレスになる**）。
- Tailscale の管理画面（Machines）で sipserver を開き、**「Disable key expiry」** にする（しないと約180日で接続が切れる）。
- `tailscale version` で動作を確認する（Pi Zero W は ARMv6 なので、念のため）。

**確認:** PCから `ping 100.x.x.x`（sipserver の Tailscale IP）が返る。PCにもTailscaleを入れて同じアカウントでログインしておく。

---

## 4. Kamailio の導入（15分） `[ ]`

PCの `G:\sipphone\server\` にある3つのファイルをPiへ送り、スクリプトを実行する。

PowerShell:
```powershell
scp G:\sipphone\server\kamailio.cfg G:\sipphone\server\wait-tailscale.conf G:\sipphone\server\memmgr.conf G:\sipphone\server\install_kamailio.sh <ユーザー名>@sipserver.local:
```
Pi:
```sh
bash ~/install_kamailio.sh
```
このスクリプトが行うこと:
- Kamailio と sngrep（SIP通信の確認ツール）をインストールする
- 設定ファイルの `TSIP` を、自分の Tailscale IP に置き換えて設置する
- メモリの割り当てを 32MB/4MB に縮小する（既定は 64MB/8MB）
- Tailscale のIPが付いてから起動するように設定し、自動起動を有効にする

**確認:** 最後の「待ち受け」に `100.x.x.x:5060` と `127.0.0.1:5060` が出る。`systemctl status kamailio` が active (running)。

### この設定の中身（kamailio.cfg）
| 機能 | 内容 |
|---|---|
| 受け付ける相手 | Tailscale網（100.64.0.0/10）と自分自身だけ。それ以外は 403 で拒否 |
| 登録（REGISTER） | 内線番号と端末の場所をメモリに保存。最長1時間 |
| 発信（INVITE） | 宛先の内線番号を探して転送する。未登録なら 404 |
| 音声 | 中継しない（端末同士が直接やり取りする）。サーバーの負荷は小さい |
| 認証 | **まだなし**（Tailscaleに参加できる＝自分の機器、が前提）。ステップ7で追加する |

クラウド環境（Kamailio 5.7）で次を確認済み: 設定の構文チェック合格、1001/1002 の登録 → 200 OK、1002→1001 の発信が転送されること、未登録宛は 404、Tailscale網以外からの登録は 403。

---

## 5. 通話テスト（30分） `[ ]`

| 端末 | アプリ | 内線 | 設定 |
|---|---|---|---|
| Windows PC | MicroSIP（無料） | 1001 | ドメイン/SIPサーバー = sipserver の 100.x.x.x、ユーザー名 1001、パスワードは空欄（任意の文字でも可） |
| スマホ | AGEphone ＋ Tailscaleアプリ | 1002 | サーバー = 100.x.x.x、ユーザー名 1002、ポート 5060、UDP |

1. 両方を登録し、表示が「登録済み」になることを確認する。
2. Piで `sudo sngrep` を起動すると、SIPのやり取りを矢印図で見られる（q で終了）。
3. 1001 から 1002 に発信 → 着信 → 応答 → 双方向に声が聞こえるか確認する。
4. コーデックは **G.711（PCMU/PCMA）を優先**にする（Pi端末側の処理を軽くするため）。

**確認:** 双方向に声が聞こえる。登録状況は `sudo kamcmd ul.dump` で見られる（登録中の内線番号と、その端末のアドレスが表示される）。

うまくいかないとき:
| 症状 | 見る場所 |
|---|---|
| 登録できない | sngrep に REGISTER が来ているか。来ていなければ Tailscale（スマホ側のVPNがオンか） |
| 403 Forbidden | Tailscale 以外の経路（自宅LANのIPなど）でサーバーにつないでいる → サーバー欄を 100.x.x.x にする |
| 呼び出せるが声が出ない | 端末側のアプリが自宅LANのIPを相手に伝えている。アプリの設定で STUN / ICE をオフにする |

---

## 6. 運用の整備（15分） `[ ]`
```sh
# journal のサイズ上限(SDへの書き込み削減)
sudo mkdir -p /etc/systemd/journald.conf.d
printf "[Journal]\nSystemMaxUse=50M\n" | sudo tee /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
# 再起動して自動で復帰するか確認
sudo reboot
```
**確認:** 再起動の約1分後に、MicroSIP がふたたび「登録済み」になる。

---

## 7. 次の段階（必要になったら）
- [ ] パスワード認証（digest認証）を追加する。Tailscale外にサーバーを公開する場合は必須。
- [ ] 端末Pi（TFT）側の SIP クライアント（baresip）との接続。
- [ ] 留守電や外線が必要になったら、Pi 4 + Asterisk へ移すことを検討する。

---

## ファイル
| ファイル | 役割 |
|---|---|
| kamailio.cfg | 最小構成の設定（`!TSIP!` はスクリプトが自動で置き換える） |
| wait-tailscale.conf | Tailscale のIPが付くまで起動を待たせる systemd の追加設定 |
| memmgr.conf | Raspbian 32bit版の Kamailio 6.0 でメモリ管理方式を TLSF に切り替える設定（qm では起動しない） |
| install_kamailio.sh | ステップ4を自動で行うスクリプト |
