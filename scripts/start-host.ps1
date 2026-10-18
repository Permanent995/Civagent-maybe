# CivAgent Host — start local brain (optional: launch SongDynasty).
# Does not touch GameInfo. Keys: repo .env or DEEPSEEK_API_KEY / OPENAI_API_KEY / ...
param(
    [switch]$LaunchGame,
    [string]$GameExe = "C:\Users\<user>\Desktop\Unciv-SongDynasty\Unciv-SongDynasty.exe",
    [string]$RepoRoot = "",
    [int]$Port = 2335,
    [int]$WaitSeconds = 45
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

$HealthUrl = "http://127.0.0.1:$Port/healthz"
$LogDir = Join-Path $RepoRoot "deployment\data\host"
$LogFile = Join-Path $LogDir "agent_api.log"
$PidFile = Join-Path $LogDir "agent_api.pid"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-Healthz {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return @{ Code = [int]$resp.StatusCode; Body = [string]$resp.Content }
    } catch {
        $ex = $_.Exception
        if ($ex.Response) {
            $code = [int]$ex.Response.StatusCode
            $body = ""
            try {
                $reader = New-Object System.IO.StreamReader($ex.Response.GetResponseStream())
                $body = $reader.ReadToEnd()
                $reader.Close()
            } catch {}
            return @{ Code = $code; Body = $body }
        }
        return @{ Code = 0; Body = [string]$ex.Message }
    }
}

Write-Host "CivAgent Host"
Write-Host "  repo : $RepoRoot"
Write-Host "  brain: $HealthUrl"

if (-not (Test-Path (Join-Path $RepoRoot ".env")) -and -not (Test-Path (Join-Path $RepoRoot "config.local.yaml"))) {
    Write-Host "  warn : no .env / config.local.yaml - copy scripts/env.example to .env and set a key"
}

$existing = Test-Healthz
if ($existing.Code -eq 200) {
    Write-Host "  brain already healthy: $($existing.Body)"
} elseif ($existing.Code -eq 503) {
    Write-Host "  brain is listening but LLM not configured (HTTP 503)."
    Write-Host "  Set DEEPSEEK_API_KEY (or OPENAI_*) in $RepoRoot\.env then restart python -m deployment.ai.agent_api"
    if ($existing.Body) { Write-Host "  detail: $($existing.Body)" }
} else {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "python not found on PATH" }

    Write-Host "  starting: python -m deployment.ai.agent_api"
    if (Test-Path $LogFile) { Remove-Item $LogFile -Force }
    $proc = Start-Process -FilePath $py.Source `
        -ArgumentList "-m", "deployment.ai.agent_api" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError $LogFile `
        -PassThru `
        -WindowStyle Hidden
    $proc.Id | Set-Content $PidFile

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $last = @{ Code = 0; Body = "" }
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        $last = Test-Healthz
        if ($last.Code -eq 200 -or $last.Code -eq 503) { break }
        if ($proc.HasExited) { break }
    }

    if ($last.Code -eq 200) {
        Write-Host "  brain ready: $($last.Body)"
    } elseif ($last.Code -eq 503) {
        Write-Host "  brain listening; LLM missing (HTTP 503). Fill .env then kill/restart PID $($proc.Id)."
        if ($last.Body) { Write-Host "  detail: $($last.Body)" }
    } else {
        Write-Host "  log: $LogFile"
        if ($proc.HasExited) { Write-Host "  exit: $($proc.ExitCode)" }
        throw "ai_server did not respond on $HealthUrl"
    }
}

if ($LaunchGame) {
    if (-not (Test-Path $GameExe)) { throw "Game exe not found: $GameExe" }
    Write-Host "  launching game: $GameExe"
    Start-Process -FilePath $GameExe -WorkingDirectory (Split-Path $GameExe)
} else {
    Write-Host "  tip: .\scripts\start-host.ps1 -LaunchGame"
}

Write-Host "Done."
