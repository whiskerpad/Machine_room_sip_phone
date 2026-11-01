#!/bin/bash
# baresip_setup.sh - 端末Piの baresip を TFT から操作できるように設定し、常駐させる
#   pi_install.sh phone から呼ばれる(手動なら: bash baresip_setup.sh [内線番号] [サーバーIP])
# 何度実行してもよい(同じ設定を上書きするだけ)
set -e
EXT=${1:-1003}
SERVER=${2:-}
[ -n "$SERVER" ] || { echo "usage: baresip_setup.sh <内線> <SIPサーバーのTailscale IP>"; exit 1; }
CARD=${AUDIO_CARD:-ANGVEPC10A}
DIR="$HOME/.baresip"
CFG="$DIR/config"
SRC=$(cd "$(dirname "$0")" && pwd)

command -v baresip >/dev/null || sudo apt-get install -y baresip-core
if [ ! -f "$CFG" ]; then
    timeout 5 baresip -f "$DIR" -t 1 </dev/null >/dev/null 2>&1 || true   # 雛形を作らせる
fi
[ -f "$CFG" ] || { echo "baresip config not created: $CFG"; exit 1; }
cp -n "$CFG" "$CFG.orig" 2>/dev/null || true

setkv() {   # setkv キー 値 : 行があれば置換(コメント外す)、無ければ追加
    if grep -qE "^#?$1[[:space:]]" "$CFG"; then
        sed -i -E "s|^#?$1[[:space:]].*|$1\t\t$2|" "$CFG"
    else
        printf '%s\t\t%s\n' "$1" "$2" >> "$CFG"
    fi
}
modon()  { sed -i -E "s|^#[[:space:]]*($1[[:space:]]+$2)|\1|" "$CFG"; grep -qE "^$1[[:space:]]+$2" "$CFG" || printf '%s\t\t%s\n' "$1" "$2" >> "$CFG"; }
modoff() { sed -i -E "s|^($1[[:space:]]+$2)|#\1|" "$CFG"; }

setkv audio_player "alsa,plughw:$CARD,0"
setkv audio_source "alsa,plughw:$CARD,0"
setkv audio_alert  "alsa,null"          # 着信音・呼出音は鳴らさない(画面表示のみ)
setkv net_interface tailscale0          # 相手に伝えるアドレスを Tailscale の IP にする
modon module alsa.so
modon module g711.so
modoff module stdio.so                  # 常駐させるのでキーボード操作は使わない
modon module_app menu.so
modon module_app ctrl_tcp.so            # TFT(wifi_ui.py)からの操作口
setkv ctrl_tcp_listen 127.0.0.1:4444

echo "<sip:$EXT@$SERVER;transport=udp>;auth_pass=x;regint=600;audio_codecs=PCMU" > "$DIR/accounts"

sed -e "s|@USER@|$USER|g" -e "s|@DIR@|$DIR|g" "$SRC/baresip.service" | sudo tee /etc/systemd/system/baresip.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable baresip >/dev/null 2>&1
sudo systemctl restart baresip
sleep 4
echo "---- config (要点) ----"
grep -E "^(audio_|net_interface|ctrl_tcp|module.*(alsa|g711|stdio|menu|ctrl_tcp))" "$CFG"
echo "---- accounts ----"; cat "$DIR/accounts"
echo "---- service ----"; systemctl --no-pager status baresip | head -n 8 || true
