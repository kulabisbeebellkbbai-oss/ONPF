[CmdletBinding()]
param(
    [string]$InstancePath,
    [string]$DatabasePath,
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
try {
    $repository = Split-Path -Parent $PSScriptRoot
    $pythonPath = Join-Path $repository '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'Complete the README first-time setup. This launcher does not install dependencies.'
    }
    if (-not $InstancePath) { $InstancePath = Join-Path $repository 'instance' }
    $InstancePath = [IO.Path]::GetFullPath($InstancePath)
    if (-not $DatabasePath) { $DatabasePath = Join-Path $InstancePath 'onpf.sqlite3' }
    $DatabasePath = [IO.Path]::GetFullPath($DatabasePath)
    $identityPath = Join-Path $InstancePath '.instance-id'
    if (-not (Test-Path -LiteralPath $identityPath -PathType Leaf) -or -not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
        throw 'Complete the README first-time setup: initialize this instance and create your account explicitly.'
    }
    # Use the same Python normalization as the service, including symlink/case handling.
    # ASCII transport also avoids legacy PowerShell's native quoting of paths.
    $pathsJson = @{ instance = $InstancePath; database = $DatabasePath } | ConvertTo-Json -Compress
    $pathsPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pathsJson))
    $instanceIdentity = & $pythonPath -c 'from pathlib import Path; import sys,json,base64; from onpf.config import readiness_identity; paths=json.loads(base64.b64decode(sys.argv[1])); print(readiness_identity(Path(paths[''instance'']), Path(paths[''database''])))' $pathsPayload
    if ($LASTEXITCODE -ne 0) { throw 'ONPF dependencies/configuration are unavailable. Review the explicit first-time setup.' }
    $instanceIdentity = $instanceIdentity.Trim()
    $url = "http://127.0.0.1:$Port"
    function Test-OnpfReady {
        try {
            $status = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 2
            return ($status.application -eq 'onpf' -and $status.version -eq '0.3.0' -and $status.instance_id -eq $instanceIdentity)
        } catch { return $false }
    }
    $socket = New-Object Net.Sockets.TcpClient
    try { $socket.Connect('127.0.0.1', $Port); $occupied = $true } catch { $occupied = $false } finally { $socket.Dispose() }
    if ($occupied) {
        if (-not (Test-OnpfReady)) { throw "Port conflict at $url. Choose another port or stop the unrelated service; no process was stopped." }
        Write-Output "Reusing ONPF at $url for the selected instance."
    } else {
        $arguments = @('-m', 'onpf.cli', '--instance', $InstancePath, 'serve', '--database', $DatabasePath, '--host', '127.0.0.1', '--port', "$Port")
        function ConvertTo-WindowsArgument([string]$value) {
            # Windows argv: double backslashes before a quote or closing quote;
            # escape embedded quotes. Other backslashes remain literal.
            $escaped = [regex]::Replace($value, '(\\*)("|$)', {
                param($match)
                $slashes = $match.Groups[1].Value
                if ($match.Groups[2].Value -eq '"') { return $slashes + $slashes + '\"' }
                return $slashes + $slashes
            })
            return '"' + $escaped + '"'
        }
        $quotedArguments = $arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }
        $started = Start-Process -FilePath $pythonPath -ArgumentList $quotedArguments -WorkingDirectory $repository -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $InstancePath 'server.stdout.log') -RedirectStandardError (Join-Path $InstancePath 'server.stderr.log')
        $ready = $false
        for ($attempt = 0; $attempt -lt 100; $attempt++) {
            if ($started.HasExited) { break }
            if (Test-OnpfReady) { $ready = $true; break }
            Start-Sleep -Milliseconds 200
        }
        if (-not $ready) {
            if (-not $started.HasExited) { Stop-Process -Id $started.Id }
            throw "ONPF did not become ready. Read server.stderr.log in the selected instance."
        }
        Set-Content -LiteralPath (Join-Path $InstancePath '.server.pid') -Value $started.Id
        Write-Output "Started ONPF at $url (process $($started.Id))."
    }
    if (-not $NoBrowser) { Start-Process -FilePath "$url/login" }
    exit 0
} catch {
    Write-Error $_
    exit 1
}
