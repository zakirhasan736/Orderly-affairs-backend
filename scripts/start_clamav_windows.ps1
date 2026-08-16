# Native ClamAV (no Docker). Run once after install, or when port 3310 is down.
$ErrorActionPreference = "Stop"
$clamBin = "C:\Program Files\ClamAV"
$cfgRoot = Join-Path $env:LOCALAPPDATA "OrderlyAffairs\ClamAV"
$dbDir = Join-Path $cfgRoot "db"
New-Item -ItemType Directory -Force -Path $dbDir | Out-Null

$clamdConf = Join-Path $cfgRoot "clamd.conf"
$freshConf = Join-Path $cfgRoot "freshclam.conf"

@"
LogFile "$cfgRoot\clamd.log"
LogTime yes
DatabaseDirectory "$dbDir"
CVDCertsDirectory "$clamBin\certs"
TCPSocket 3310
TCPAddr 127.0.0.1
MaxThreads 4
MaxQueue 20
StreamMaxLength 32M
ScanPDF yes
ScanOLE2 yes
ScanSWF yes
ScanXMLDOCS yes
ScanHWP3 yes
DetectPUA yes
Foreground no
"@ | Set-Content -Encoding ascii $clamdConf

@"
DatabaseDirectory "$dbDir"
CVDCertsDirectory "$clamBin\certs"
UpdateLogFile "$cfgRoot\freshclam.log"
LogTime yes
DatabaseMirror database.clamav.net
Checks 12
NotifyClamd "$clamdConf"
"@ | Set-Content -Encoding ascii $freshConf

Write-Host "Updating virus definitions (first run can take several minutes)..."
& "$clamBin\freshclam.exe" --config-file="$freshConf"
if ($LASTEXITCODE -ne 0) {
  throw "freshclam failed with exit $LASTEXITCODE"
}

Get-Process clamd -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -FilePath "$clamBin\clamd.exe" -ArgumentList "--config-file=`"$clamdConf`"" -WindowStyle Hidden
Write-Host "clamd started. Waiting for 127.0.0.1:3310..."
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 2
  try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", 3310)
    $tcp.Close()
    $ok = $true
    break
  } catch { }
}
if (-not $ok) { throw "clamd did not open port 3310" }
Write-Host "ClamAV is listening on 127.0.0.1:3310"
