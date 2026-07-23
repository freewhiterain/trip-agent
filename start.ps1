param(
    [switch]$SkipInfrastructure,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Read-DotEnv {
    $values = @{}
    if (Test-Path ".env") {
        foreach ($line in Get-Content ".env") {
            if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
                $values[$Matches[1]] = $Matches[2].Trim().Trim('"')
            }
        }
    }
    return $values
}

function Get-ConfiguredValue {
    param([hashtable]$Values, [string]$Name, [string]$Default)
    if ($Values.ContainsKey($Name) -and $Values[$Name]) {
        return $Values[$Name]
    }
    return $Default
}

function Invoke-CheckedCommand {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

try {
    $dotEnv = Read-DotEnv
    $appPort = [int](Get-ConfiguredValue $dotEnv "APP_PORT" "18000")
    $postgresHostPort = [int](Get-ConfiguredValue $dotEnv "POSTGRES_PORT" "15432")
    $redisHostPort = [int](Get-ConfiguredValue $dotEnv "REDIS_PORT" "16379")
    $appUrl = "http://127.0.0.1:$appPort"
    $docsUrl = "$appUrl/docs"
    $frontendFile = Join-Path $projectRoot "1_zhixing.html"
    $python = Join-Path $projectRoot ".venv\Scripts\python.exe"

    if (-not (Test-Path $python)) {
        throw "Virtual environment .venv was not found."
    }

    $listening = Get-NetTCPConnection -LocalPort $appPort -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        throw "Application port $appPort is already in use."
    }

    if ($CheckOnly) {
        Write-Host "Startup configuration valid: APP_PORT=$appPort POSTGRES_PORT=$postgresHostPort REDIS_PORT=$redisHostPort"
        exit 0
    }

    if (-not $SkipInfrastructure) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker was not found. Install and start Docker Desktop first."
        }

        Invoke-CheckedCommand "docker" @("info")

        Invoke-CheckedCommand "docker" @("volume", "create", "trip_postgres_data")
        $containerNames = @(docker container ls -a --format "{{.Names}}")
        if ($containerNames -notcontains "travel_postgres") {
            Invoke-CheckedCommand "docker" @(
                "run", "-d", "--name", "travel_postgres",
                "-e", "POSTGRES_DB=ai_travel_db",
                "-e", "POSTGRES_USER=travel_user",
                "-e", "POSTGRES_PASSWORD=travel123456",
                "-p", "$postgresHostPort`:5432",
                "-v", "trip_postgres_data:/var/lib/postgresql/data",
                "--restart", "unless-stopped", "pgvector/pgvector:pg17"
            )
        } else {
            Invoke-CheckedCommand "docker" @("start", "travel_postgres")
        }

        $postgresReady = $false
        for ($attempt = 1; $attempt -le 30; $attempt++) {
            docker exec travel_postgres pg_isready -U travel_user -d ai_travel_db 2>$null
            if ($LASTEXITCODE -eq 0) {
                $postgresReady = $true
                break
            }
            Start-Sleep -Seconds 2
        }
        if (-not $postgresReady) {
            throw "PostgreSQL was not ready within 60 seconds."
        }

        Invoke-CheckedCommand "docker" @("volume", "create", "trip_redis_data")
        $containerNames = @(docker container ls -a --format "{{.Names}}")
        if ($containerNames -notcontains "travel_redis") {
            Invoke-CheckedCommand "docker" @(
                "run", "-d", "--name", "travel_redis", "-p", "$redisHostPort`:6379",
                "-v", "trip_redis_data:/data", "--restart", "unless-stopped",
                "redis:7-alpine", "redis-server", "--appendonly", "yes"
            )
        } else {
            Invoke-CheckedCommand "docker" @("start", "travel_redis")
        }

        $redisReady = $false
        for ($attempt = 1; $attempt -le 30; $attempt++) {
            $redisPing = docker exec travel_redis redis-cli ping 2>$null
            if ($LASTEXITCODE -eq 0 -and $redisPing -match "PONG") {
                $redisReady = $true
                break
            }
            Start-Sleep -Seconds 2
        }
        if (-not $redisReady) {
            throw "Redis was not ready within 60 seconds."
        }

        & $python "scripts\init_db.py"
        if ($LASTEXITCODE -ne 0) {
            throw "Database initialization failed."
        }
    }

    $frontendForCommand = $frontendFile.Replace("'", "''")
    $healthCommand = @"
`$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt `$deadline) {
    try {
        Invoke-WebRequest -UseBasicParsing '$appUrl/' -TimeoutSec 2 | Out-Null
        Start-Process -FilePath '$frontendForCommand'
        exit 0
    } catch {
        Start-Sleep -Seconds 1
    }
}
"@
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-WindowStyle", "Hidden", "-Command", $healthCommand
    ) | Out-Null

    Write-Host "Starting service: $appUrl"
    Write-Host "API docs: $docsUrl"
    & $python "app\run.py"
    exit $LASTEXITCODE
} catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
