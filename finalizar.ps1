[CmdletBinding(SupportsShouldProcess=$true)]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$venvRoot = (Join-Path $projectRoot '.venv\Scripts') + '\'
$tunnelPath = Join-Path $projectRoot '.conexion\cloudflared.exe'
$serverPath = Join-Path $projectRoot 'server.py'
$browserPath = Join-Path $projectRoot 'abrir_navegador.py'
$all = @(Get-CimInstance Win32_Process)
$owned = @{}
foreach ($process in $all) {
    $exe = [string]$process.ExecutablePath
    $command = [string]$process.CommandLine
    $python = $process.Name -match '^pythonw?([0-9.]*)?\.exe$'
    $absoluteScript = $command -match ('(?:^|\s)"?' + [regex]::Escape($serverPath) + '"?(?:\s|$)') -or $command -match ('(?:^|\s)"?' + [regex]::Escape($browserPath) + '"?(?:\s|$)')
    if (($python -and ($exe.StartsWith($venvRoot,[StringComparison]::OrdinalIgnoreCase) -or $absoluteScript)) -or $exe -eq $tunnelPath) {
        $owned[[int]$process.ProcessId] = $process
    }
}
# Include Python children of the virtual environment launcher.
do {
    $added = $false
    foreach ($process in $all) {
        if ($owned.ContainsKey([int]$process.ParentProcessId) -and -not $owned.ContainsKey([int]$process.ProcessId) -and $process.Name -match '^pythonw?([0-9.]*)?\.exe$') {
            $owned[[int]$process.ProcessId] = $process
            $added = $true
        }
    }
} while ($added)
foreach ($process in @($owned.Values | Sort-Object ParentProcessId -Descending)) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)"
    if ($current -and $current.CreationDate -eq $process.CreationDate -and $PSCmdlet.ShouldProcess("$($process.Name) PID $($process.ProcessId)", 'Cerrar proceso del proyecto')) {
        Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
    }
}
if ($PSCmdlet.ShouldProcess($projectRoot, 'Marcar enlace del celular desconectado')) {
    $savedTunnel = Join-Path $projectRoot '.conexion\tunnel.json'
    if (Test-Path -LiteralPath $savedTunnel) { Remove-Item -LiteralPath $savedTunnel }
    Set-Content -LiteralPath (Join-Path $projectRoot 'ENLACE_CELULAR.txt') -Value 'DESCONECTADO. Ejecuta CONECTAR_CELULAR.bat para volver a conectar.' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $projectRoot 'ENLACE_CELULAR.html') -Value '<!doctype html><meta charset="utf-8"><h1>Sistema detenido</h1><p>Ejecuta INICIAR.bat para iniciar o CONECTAR_CELULAR.bat para acceso desde el celular.</p>' -Encoding UTF8
}
Write-Host 'Finalizacion completada. Los datos se conservan. Puedes cerrar las ventanas de inicio.'
