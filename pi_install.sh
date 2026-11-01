#!/bin/bash
# pi_install.sh - deploy.ps1 から呼ばれる (Pi側)
set -e
MODE=${1:-install}
SRC=~/sipphone_tmp
DST=/opt/sipphone

tr -d '\r' < "$SRC/wifi_ui.py" > "$SRC/wifi_ui.py.lf"
tr -d '\r' < "$SRC/wifi-ui.service" > "$SRC/wifi-ui.service.lf"
tr -d '\r' < "$SRC/captive_lib.py" > "$SRC/captive_lib.py.lf"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$SRC/wifi_ui.py.lf"
tr -d '\r' < "$SRC/phone.py" > "$SRC/phone.py.lf"
for f in baresip_setup.sh baresip.service; do
    tr -d '\r' < "$SRC/$f" > "$SRC/$f.tmp" && mv "$SRC/$f.tmp" "$SRC/$f"
done
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$SRC/captive_lib.py.lf"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$SRC/phone.py.lf"
echo "syntax OK"

sudo mkdir -p "$DST"
sudo install -m 755 "$SRC/wifi_ui.py.lf" "$DST/wifi_ui.py"
sudo install -m 644 "$SRC/captive_lib.py.lf" "$DST/captive_lib.py"
sudo install -m 644 "$SRC/phone.py.lf" "$DST/phone.py"
sudo install -m 644 "$SRC/wifi-ui.service.lf" /etc/systemd/system/wifi-ui.service
sudo systemctl daemon-reload
echo "installed to $DST"

if [ "$MODE" = "setup" ]; then
    sudo apt-get update
    sudo apt-get install -y python3-serial
    sudo raspi-config nonint do_serial_cons 1
    sudo raspi-config nonint do_serial_hw 0
    CFG=/boot/firmware/config.txt
    [ -f "$CFG" ] || CFG=/boot/config.txt
    grep -q '^dtoverlay=disable-bt' "$CFG" || echo 'dtoverlay=disable-bt' | sudo tee -a "$CFG" >/dev/null
    sudo systemctl disable hciuart 2>/dev/null || true
    sudo systemctl enable wifi-ui
    echo "setup done. rebooting..."
    sudo reboot
    exit 0
fi

if [ "$MODE" = "phone" ]; then
    # baresip を設定して常駐させる(内線番号は第2引数、既定 1003)
    bash "$SRC/baresip_setup.sh" "${2:-1003}" "${3:-}"
fi

if systemctl is-enabled --quiet wifi-ui 2>/dev/null; then
    sudo systemctl restart wifi-ui
    sleep 3
    systemctl --no-pager status wifi-ui | head -n 12 || true
else
    echo "wifi-ui service is not enabled (run deploy.bat -Setup once)"
fi
