# deploy.ps1 - G:\sipphone のコードを Pi Zero へ送って反映する
# 使い方 (deploy.bat 経由を推奨):
#   deploy.bat              コードを送ってサービス再起動
#   deploy.bat -Key         初回のみ: SSH鍵を登録 (以後パスワード入力不要)
#   deploy.bat -Setup       初回のみ: UART設定 / python3-serial / 自動起動有効化 / 再起動
#   deploy.bat -Phone       baresip(内線1003)を設定して常駐させる (初回と設定変更時)
#   deploy.bat -Phone -Ext 1004 -SipServer 100.x.y.z   内線番号/サーバーを指定
#   deploy.bat -Logs        Pi の captive_logs を G:\sipphone\captive_logs へ取得
#   deploy.bat -PiHost 192.168.1.50 -PiUser foo   接続先を一時的に変更
param(
    [string]$PiHost = "sipphone.local",   # ← 自分のPiのホスト名 or IP に書き換え
    [string]$PiUser = "pi",                  # ← Imagerで作ったユーザー名に書き換え
    [switch]$Setup,
    [switch]$Key,
    [switch]$Logs,
    [switch]$Phone,
    [string]$Ext = "1003",                   # -Phone 時の内線番号
    [string]$SipServer = ""     # -Phone 時の SIPサーバー(Tailscale IP)
)
$ErrorActionPreference = "Stop"
$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = "$PiUser@$PiHost"

function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$exe failed (exit $LASTEXITCODE)" }
}

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "OpenSSH client not found. Settings > Apps > Optional features > OpenSSH Client"
}
Write-Host "Target: $Target" -ForegroundColor Cyan

if ($Key) {
    $keyPath = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
    if (-not (Test-Path "$keyPath.pub")) {
        Write-Host "Generating SSH key (passphrase: just press Enter twice)"
        Run "ssh-keygen" @("-t", "ed25519", "-f", $keyPath)
    }
    Get-Content "$keyPath.pub" | ssh $Target "mkdir -p ~/.ssh && chmod 700 ~/.ssh && tr -d '\r' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    if ($LASTEXITCODE -ne 0) { throw "key registration failed" }
    Write-Host "SSH key registered." -ForegroundColor Green
    exit 0
}

if ($Logs) {
    $dst = Join-Path $Here "captive_logs"
    New-Item -ItemType Directory -Force $dst | Out-Null
    & scp -r "${Target}:/opt/sipphone/captive_logs/*" $dst
    if ($LASTEXITCODE -ne 0) { Write-Host "No logs or copy failed." -ForegroundColor Yellow; exit 1 }
    Write-Host "Logs -> $dst" -ForegroundColor Green
    exit 0
}

$files = @("wifi_ui.py", "wifi-ui.service", "pi_install.sh", "pc_tools\captive_lib.py",
           "phone.py", "baresip_setup.sh", "baresip.service") | ForEach-Object {
    $p = Join-Path $Here $_
    if (-not (Test-Path $p)) { throw "missing: $p" }
    $p
}

Run "ssh" @($Target, "mkdir -p ~/sipphone_tmp")
Run "scp" ($files + @("${Target}:sipphone_tmp/"))

$mode = if ($Setup) { "setup" } elseif ($Phone) { "phone" } else { "install" }
& ssh -t $Target "bash ~/sipphone_tmp/pi_install.sh $mode $Ext $SipServer"
if ($LASTEXITCODE -ne 0 -and -not $Setup) { throw "install failed (exit $LASTEXITCODE)" }
Write-Host "Done." -ForegroundColor Green
