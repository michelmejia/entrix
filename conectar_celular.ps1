param([switch]$Stop, [switch]$NoOpen)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$runtime = Join-Path $root '.conexion'
$binary = Join-Path $runtime 'cloudflared.exe'
$stateFile = Join-Path $runtime 'tunnel.json'
$linkFile = Join-Path $root 'ENLACE_CELULAR.txt'
$pageFile = Join-Path $root 'ENLACE_CELULAR.html'
$origin = 'http://127.0.0.1:5000'
$version = '2026.9.1'
$expectedHash = '2837888cc0f5d58f15b6dc478376de90b4d3ba5241c7947455d1e0a0df429712'

function Get-LocalStatus {
    try { return Invoke-RestMethod -Uri "$origin/api/auth/status" -TimeoutSec 3 }
    catch { return $null }
}

function Get-OwnedTunnel($saved) {
    if (-not $saved) { return $null }
    $running = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
    if ($running -and $running.Path -eq $binary -and $running.StartTime.ToUniversalTime().Ticks.ToString() -eq $saved.started) { return $running }
    return $null
}

function Show-Link([string]$url) {
    if ($url -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') { throw 'El enlace recibido no es un enlace valido de Cloudflare.' }
    Set-Content -LiteralPath $linkFile -Value $url -Encoding UTF8
    $html = @"
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Acceso desde el celular</title><style>body{font:18px system-ui;background:#0b1120;color:#edf3fb;max-width:650px;margin:50px auto;padding:24px}h1{font-size:30px}p{line-height:1.6}a{color:#57e3ba;overflow-wrap:anywhere}#qr{background:white;padding:24px;width:240px;margin:28px 0}small{color:#a9b7ce}</style><script src="static/vendor/qrcode.min.js"></script></head><body><h1>Abre Acceso en tu celular</h1><p>Escanea este QR con la camara del telefono o abre el enlace.</p><div id="qr"></div><p><a href="$url">$url</a></p><p>Inicia sesion, entra a Validar ingreso y permite el uso de la camara.</p><small>Manten esta PC encendida y conectada a Internet. Este enlace es temporal y puede cambiar al volver a conectar. Para detenerlo, ejecuta DESCONECTAR_CELULAR.bat.</small><script>new QRCode(document.getElementById('qr'),{text:'$url',width:240,height:240});</script></body></html>
"@
    Set-Content -LiteralPath $pageFile -Value $html -Encoding UTF8
    Write-Host "`nENLACE PARA EL CELULAR: $url`n" -ForegroundColor Green
    Write-Host 'Puedes cerrar esta ventana. El enlace sigue activo mientras la PC este encendida.'
    Write-Host 'Para detenerlo: DESCONECTAR_CELULAR.bat'
    if (-not $NoOpen) { Start-Process -FilePath $pageFile -WindowStyle Hidden }
}

$saved = $null
if (Test-Path -LiteralPath $stateFile) {
    try { $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json } catch { }
}
$existing = Get-OwnedTunnel $saved
if ($Stop) {
    if ($existing) { Stop-Process -Id $existing.Id; Write-Host 'Enlace del celular desactivado. El servidor local sigue funcionando.' }
    else { Write-Host 'No hay un enlace activo iniciado por este proyecto.' }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile }
    if (Test-Path -LiteralPath $linkFile) { Set-Content -LiteralPath $linkFile -Value 'DESCONECTADO. Ejecuta CONECTAR_CELULAR.bat para generar un enlace.' -Encoding UTF8 }
    if (Test-Path -LiteralPath $pageFile) { Set-Content -LiteralPath $pageFile -Value '<!doctype html><meta charset="utf-8"><h1>Enlace desconectado</h1><p>Ejecuta CONECTAR_CELULAR.bat para generar un nuevo enlace.</p>' -Encoding UTF8 }
    exit 0
}

New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$status = Get-LocalStatus
if (-not $status) {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Ejecuta INICIAR.bat una vez para preparar Python.' }
    Write-Host 'Iniciando servidor local...'
    $env:ACCESO_HTTPS = '1'
    $env:ACCESO_HOST = '127.0.0.1'
    $env:PORT = '5000'
    Start-Process -FilePath $python -ArgumentList ('"' + (Join-Path $root 'server.py') + '"') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'server.log') -RedirectStandardError (Join-Path $runtime 'server-error.log') | Out-Null
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        $status = Get-LocalStatus
        if ($status) { break }
    }
}
if (-not $status -or $null -eq $status.setupRequired) { throw 'El servidor Acceso no responde en el puerto 5000. Revisa INICIAR.bat.' }
if ($status.setupRequired) {
    Write-Host 'Primero crea tu administrador desde http://127.0.0.1:5000.'
    $serverLog = Join-Path $runtime 'server.log'
    if (Test-Path -LiteralPath $serverLog) { Get-Content -LiteralPath $serverLog | Select-String 'instalaci' | ForEach-Object { Write-Host $_.Line } }
    throw 'Vuelve a ejecutar CONECTAR_CELULAR.bat despues de crear el administrador.'
}
if ($existing -and $saved.url) { Show-Link $saved.url; exit 0 }
if ($existing) { throw 'El tunel todavia esta iniciando. Espera unos segundos; si no aparece el enlace, desconecta y vuelve a conectar.' }

if (-not (Test-Path -LiteralPath $binary) -or (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    Write-Host "Descargando cloudflared oficial $version (solo la primera vez)..."
    $download = Join-Path $runtime 'cloudflared.download'
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/cloudflare/cloudflared/releases/download/$version/cloudflared-windows-amd64.exe" -OutFile $download -TimeoutSec 180
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'La descarga no coincide con la firma SHA-256 publicada. No se ejecutara.' }
    Move-Item -LiteralPath $download -Destination $binary -Force
}
$config = Join-Path $runtime 'quick.yml'
Set-Content -LiteralPath $config -Value 'no-autoupdate: true' -Encoding ASCII
$log = Join-Path $runtime 'tunnel.log'
$outLog = Join-Path $runtime 'tunnel-stdout.log'
$argsList = @('tunnel','--config',('"'+$config+'"'),'--no-autoupdate','--protocol','http2','--url',$origin)
Write-Host 'Creando enlace HTTPS...'
$tunnel = Start-Process -FilePath $binary -ArgumentList $argsList -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardError $log -RedirectStandardOutput $outLog -PassThru
$state = @{ pid=$tunnel.Id; started=$tunnel.StartTime.ToUniversalTime().Ticks.ToString(); url=$null }
$state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
$url = $null
for ($attempt=0; $attempt -lt 90; $attempt++) {
    Start-Sleep -Seconds 1
    if ($tunnel.HasExited) { throw "Cloudflare no pudo iniciar. Revisa $log" }
    $content = Get-Content -LiteralPath $log -Raw -ErrorAction SilentlyContinue
    if ($content -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $url=$Matches[0]; break }
}
if (-not $url) { Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue; throw "No se recibio un enlace. Revisa $log" }
$state.url = $url
$state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
Write-Host 'Comprobando acceso desde Internet...'
$ready = $false
for ($attempt=0; $attempt -lt 15; $attempt++) {
    try {
        $remote = Invoke-RestMethod -Uri "$url/api/auth/status" -TimeoutSec 5
        if ($remote.setupRequired -eq $false) { $ready=$true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $ready) { Write-Host 'El enlace fue creado pero la comprobacion remota aun no responde. Puede tardar un momento.' -ForegroundColor Yellow }
Show-Link $url
