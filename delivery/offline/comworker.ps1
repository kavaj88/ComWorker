param(
    [ValidateSet('status', 'start', 'stop', 'restart', 'logs', 'backup', 'upgrade', 'uninstall')]
    [string]$Action = 'status',
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Compose = @('compose', '-p', 'comworker-offline', '-f', 'compose.yml')

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @Compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose 命令失败" }
}

function Backup-ComWorker {
    New-Item -ItemType Directory -Force -Path backups | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $settings = @{}
    Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] } }
    & docker @Compose exec -T postgres pg_dump -U $settings['POSTGRES_USER'] $settings['POSTGRES_DB'] |
        Set-Content -Encoding utf8 "backups/database-$stamp.sql"
    if ($LASTEXITCODE -ne 0) { throw '数据库备份失败' }
    Compress-Archive -Force -Path .env, secrets -DestinationPath "backups/config-$stamp.zip"
    Write-Host "备份完成: backups/*-$stamp.*"
}

switch ($Action) {
    'status' { Invoke-Compose ps }
    'start' { Invoke-Compose up -d }
    'stop' { Invoke-Compose stop }
    'restart' { Invoke-Compose restart }
    'logs' { Invoke-Compose logs -f --tail=200 }
    'backup' { Backup-ComWorker }
    'upgrade' { Backup-ComWorker; & "$Root\install.ps1" }
    'uninstall' {
        if ($Purge) {
            $answer = Read-Host '这将永久删除数据库和所有用户数据。请输入 DELETE 确认'
            if ($answer -ne 'DELETE') { throw '已取消' }
            Invoke-Compose down -v --remove-orphans
        } else {
            Invoke-Compose down --remove-orphans
            Write-Host '容器已移除，数据卷和密钥已保留。'
        }
    }
}
