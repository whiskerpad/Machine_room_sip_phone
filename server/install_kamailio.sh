#!/bin/bash
# install_kamailio.sh - Pi Zero W に Kamailio(最小構成) を入れて起動する
#   前提: Tailscale にログイン済み (tailscale ip -4 で 100.x.x.x が出る)
#   使い方: このフォルダ(kamailio.cfg, wait-tailscale.conf, memmgr.conf と同じ場所)で
#           bash install_kamailio.sh
set -e
cd "$(dirname "$0")"

TSIP=$(tailscale ip -4 2>/dev/null | head -n1)
if [ -z "$TSIP" ]; then
    echo "Tailscale の IP が取れません。先に 'sudo tailscale up' を実行してください。"; exit 1
fi
echo "Tailscale IP: $TSIP"

sudo apt-get update
sudo apt-get install -y kamailio sngrep

# 設定ファイル(既存はバックアップ)
[ -f /etc/kamailio/kamailio.cfg ] && sudo cp -n /etc/kamailio/kamailio.cfg /etc/kamailio/kamailio.cfg.orig
tr -d '\r' < kamailio.cfg | sed "s/!TSIP!100.64.0.1!/!TSIP!$TSIP!/" | sudo tee /etc/kamailio/kamailio.cfg >/dev/null

# メモリ割り当てを Pi Zero 向けに縮小 (既定 64MB/8MB → 32MB/4MB)
sudo tee /etc/default/kamailio >/dev/null <<EOT
SHM_MEMORY=32
PKG_MEMORY=4
EOT

# Tailscale の IP が付いてから起動させる
sudo mkdir -p /etc/systemd/system/kamailio.service.d
tr -d '\r' < wait-tailscale.conf | sudo tee /etc/systemd/system/kamailio.service.d/wait-tailscale.conf >/dev/null

# Raspbian(32bit)版はメモリ管理 qm が使えないので TLSF を使う(memmgr.conf)
tr -d '\r' < memmgr.conf | sudo tee /etc/systemd/system/kamailio.service.d/memmgr.conf >/dev/null
sudo kamailio -c -f /etc/kamailio/kamailio.cfg -M 4 -m 32 -X tlsf -x tlsf
sudo systemctl daemon-reload
sudo systemctl enable kamailio
sudo systemctl restart kamailio
sleep 2
systemctl --no-pager status kamailio | head -n 8
echo
echo "待ち受け:"; sudo ss -lunp | grep 5060 || true
echo
echo "完了。端末の SIP サーバー欄には $TSIP を入れる。"
