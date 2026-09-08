param(
    [ValidateSet('status', 'start', 'stop', 'restart', 'logs', 'backup', 'upgrade', 'restore', 'rollback', 'reset-admin-password', 'uninstall')]
    [string]$Action = 'status',
    [string]$PackagePath,
    [string]$BackupPath,
    [string]$SourceDir,
    [string]$NewPassword,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Compose = @('compose', '-p', 'comworker-offline', '-f', (Join-Path $Root 'compose.yml'))
$HelperImage = 'comworker-postgres:16-alpine'

function Write-Utf8NoBom([string]$Path, [string[]]$Lines) {
    [IO.File]::WriteAllLines($Path, $Lines, [Text.UTF8Encoding]::new($false))
}

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker 命令失败: docker $($Arguments -join ' ')" }
}

function Get-Settings([string]$Directory) {
    $settings = @{}
    Get-Content -LiteralPath (Join-Path $Directory '.env') | ForEach-Object {
        if ($_ -match '^([^#=]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] }
    }
    return $settings
}

function Get-ReleaseVersion([string]$Directory) {
    foreach ($file in @('.comworker-install.json', 'manifest.json')) {
        $path = Join-Path $Directory $file
        if (Test-Path -LiteralPath $path) {
            try {
                $value = (Get-Content -Raw -LiteralPath $path | ConvertFrom-Json).version
                if ($value) { return [string]$value }
            } catch {}
        }
    }
    $settings = Get-Settings $Directory
    if ($settings.ContainsKey('COMWORKER_VERSION')) { return [string]$settings['COMWORKER_VERSION'] }
    return 'unknown'
}

function Backup-ComWorker([string]$Directory = $Root) {
    $Directory = (Resolve-Path -LiteralPath $Directory).Path
    foreach ($required in @('.env', 'secrets', 'compose.yml')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Directory $required))) { throw "安装目录缺少 $required" }
    }
    $sourceCompose = @('compose', '-p', 'comworker-offline', '--env-file', (Join-Path $Directory '.env'), '-f', (Join-Path $Directory 'compose.yml'))
    $postgresId = (& docker @sourceCompose ps -q postgres).Trim()
    if (-not $postgresId) { throw '数据库未运行，请先启动 ComWorker 再备份' }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupDir = Join-Path $Root "backups\backup-$stamp"
    $volumeDir = Join-Path $backupDir 'volumes'
    $configDir = Join-Path $backupDir 'config'
    New-Item -ItemType Directory -Force -Path $volumeDir, (Join-Path $configDir 'secrets') | Out-Null
    $settings = Get-Settings $Directory
    $dbUser = if ($settings.ContainsKey('POSTGRES_USER')) { $settings['POSTGRES_USER'] } else { 'comworker' }
    $dbName = if ($settings.ContainsKey('POSTGRES_DB')) { $settings['POSTGRES_DB'] } else { 'comworker_platform' }

    Invoke-Docker (@($sourceCompose) + @('exec', '-T', 'postgres', 'pg_dump', '-Fc', '-U', $dbUser, '-f', '/tmp/comworker-backup.dump', $dbName))
    Invoke-Docker @('cp', "${postgresId}:/tmp/comworker-backup.dump", (Join-Path $backupDir 'database.dump'))
    Invoke-Docker (@($sourceCompose) + @('exec', '-T', 'postgres', 'rm', '-f', '/tmp/comworker-backup.dump'))
    if ((Get-Item -LiteralPath (Join-Path $backupDir 'database.dump')).Length -eq 0) { throw '数据库备份为空' }

    Copy-Item -LiteralPath (Join-Path $Directory '.env') -Destination (Join-Path $configDir '.env')
    Get-ChildItem -LiteralPath (Join-Path $Directory 'secrets') -Force | Copy-Item -Destination (Join-Path $configDir 'secrets') -Recurse -Force
    foreach ($file in @('.comworker-install.json', 'manifest.json')) {
        $source = Join-Path $Directory $file
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $configDir $file) }
    }

    $volumes = [Collections.Generic.List[string]]::new()
    foreach ($volume in @('comworker-offline-platform-data', 'comworker-offline-platform-skills', 'comworker-offline-builtin-skills')) {
        & docker volume inspect $volume *> $null
        if ($LASTEXITCODE -eq 0) { $volumes.Add($volume) }
    }
    (& docker volume ls --format '{{.Name}}') | Where-Object { $_ -like 'hermes-data-*' } | ForEach-Object {
        if (-not $volumes.Contains($_)) { $volumes.Add($_) }
    }
    foreach ($volume in $volumes) {
        Write-Host "==> 备份数据卷 $volume"
        Invoke-Docker @('run', '--rm', '-e', "ARCHIVE_NAME=$volume.tar.zst", '-v', "${volume}:/source:ro", '-v', "${volumeDir}:/backup", '--entrypoint', 'sh', $HelperImage, '-c', 'set -eu; tar -C /source -cf - . | zstd -T0 -q -o "/backup/$ARCHIVE_NAME"')
    }

    $manifest = [ordered]@{
        product = 'ComWorker'
        version = Get-ReleaseVersion $Directory
        install_dir = $Directory
        created_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        database = 'database.dump'
        volumes = @($volumes)
    }
    Write-Utf8NoBom (Join-Path $backupDir 'backup-manifest.json') @($manifest | ConvertTo-Json -Depth 4)
    $checksumLines = Get-ChildItem -LiteralPath $backupDir -Recurse -File |
        Where-Object { $_.Name -ne 'checksums.sha256' } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($backupDir.Length + 1).Replace('\', '/')
            "$(($_ | Get-FileHash -Algorithm SHA256).Hash.ToLowerInvariant())  $relative"
        }
    Write-Utf8NoBom (Join-Path $backupDir 'checksums.sha256') @($checksumLines)
    Write-Host "备份完成: $backupDir"
    return $backupDir
}

function Confirm-Restore {
    $answer = Read-Host '恢复会覆盖当前数据库和数据。请输入 RESTORE 确认'
    if ($answer -ne 'RESTORE') { throw '已取消' }
}

function Test-Backup([string]$Directory) {
    foreach ($required in @('backup-manifest.json', 'checksums.sha256', 'database.dump', 'config/.env', 'config/secrets')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Directory $required))) { throw "备份缺少 $required" }
    }
    foreach ($line in Get-Content -LiteralPath (Join-Path $Directory 'checksums.sha256')) {
        if ($line -notmatch '^([0-9a-fA-F]{64})\s+(.+)$') { throw '备份校验清单格式错误' }
        $path = Join-Path $Directory $Matches[2].Replace('/', [IO.Path]::DirectorySeparatorChar)
        if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -ne $Matches[1].ToLowerInvariant()) {
            throw "备份校验失败: $($Matches[2])"
        }
    }
}

function Restore-ComWorker([string]$Directory, [switch]$NoStart) {
    $Directory = (Resolve-Path -LiteralPath $Directory).Path
    Test-Backup $Directory
    Confirm-Restore
    Invoke-Docker (@($Compose) + @('down', '--remove-orphans'))
    foreach ($archive in Get-ChildItem -LiteralPath (Join-Path $Directory 'volumes') -Filter '*.tar.zst' -File -ErrorAction SilentlyContinue) {
        $volume = $archive.Name.Substring(0, $archive.Name.Length - '.tar.zst'.Length)
        & docker volume inspect $volume *> $null
        if ($LASTEXITCODE -ne 0) { Invoke-Docker @('volume', 'create', $volume) | Out-Null }
        (& docker ps -q --filter "volume=$volume") | ForEach-Object { if ($_) { Invoke-Docker @('stop', $_) | Out-Null } }
        Write-Host "==> 恢复数据卷 $volume"
        Invoke-Docker @('run', '--rm', '-e', "ARCHIVE_NAME=$($archive.Name)", '-v', "${volume}:/target", '-v', "$($archive.DirectoryName):/backup:ro", '--entrypoint', 'sh', $HelperImage, '-c', 'set -eu; find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; zstd -dc "/backup/$ARCHIVE_NAME" | tar -C /target -xf -')
    }

    Copy-Item -LiteralPath (Join-Path $Directory 'config/.env') -Destination (Join-Path $Root '.env') -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'secrets') | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $Directory 'config/secrets') -Force | Copy-Item -Destination (Join-Path $Root 'secrets') -Recurse -Force
    $savedState = Join-Path $Directory 'config/.comworker-install.json'
    if (Test-Path -LiteralPath $savedState) { Copy-Item -LiteralPath $savedState -Destination (Join-Path $Root '.comworker-install.json') -Force }
    $env:ADMIN_PASSWORD = Get-Content -Raw -LiteralPath (Join-Path $Root 'secrets/admin_password')
    $settings = Get-Settings $Root
    $dbUser = if ($settings.ContainsKey('POSTGRES_USER')) { $settings['POSTGRES_USER'] } else { 'comworker' }
    $dbName = if ($settings.ContainsKey('POSTGRES_DB')) { $settings['POSTGRES_DB'] } else { 'comworker_platform' }
    Invoke-Docker (@($Compose) + @('up', '-d', 'postgres'))
    $postgresId = (& docker @Compose ps -q postgres).Trim()
    $deadline = (Get-Date).AddSeconds(90)
    do {
        & docker @Compose exec -T postgres pg_isready -U $dbUser *> $null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if ((Get-Date) -ge $deadline) { throw '数据库恢复前未能就绪' }
    Invoke-Docker @('cp', (Join-Path $Directory 'database.dump'), "${postgresId}:/tmp/comworker-restore.dump")
    & docker @Compose exec -T postgres dropdb --if-exists --force -U $dbUser $dbName
    Invoke-Docker (@($Compose) + @('exec', '-T', 'postgres', 'createdb', '-U', $dbUser, $dbName))
    Invoke-Docker (@($Compose) + @('exec', '-T', 'postgres', 'pg_restore', '--exit-on-error', '-U', $dbUser, '-d', $dbName, '/tmp/comworker-restore.dump'))
    Invoke-Docker (@($Compose) + @('exec', '-T', 'postgres', 'rm', '-f', '/tmp/comworker-restore.dump'))
    if (-not $NoStart) {
        Invoke-Docker (@($Compose) + @('up', '-d'))
        Write-Host "恢复完成: $Directory"
    }
}

switch ($Action) {
    'status' { Invoke-Docker (@($Compose) + @('ps')) }
    'start' { Invoke-Docker (@($Compose) + @('up', '-d')) }
    'stop' { Invoke-Docker (@($Compose) + @('stop')) }
    'restart' { Invoke-Docker (@($Compose) + @('restart')) }
    'logs' { Invoke-Docker (@($Compose) + @('logs', '-f', '--tail=200')) }
    'backup' { Backup-ComWorker $(if ($SourceDir) { $SourceDir } else { $Root }) | Out-Null }
    'upgrade' {
        if (-not $PackagePath) { throw '请使用 -PackagePath 填写已解压的新版本安装目录' }
        $newRoot = (Resolve-Path -LiteralPath $PackagePath).Path
        if (-not (Test-Path -LiteralPath (Join-Path $newRoot 'install.ps1'))) { throw '新版本目录缺少 install.ps1' }
        Backup-ComWorker $Root | Out-Null
        $env:COMWORKER_BACKUP_DONE = '1'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $newRoot 'install.ps1') -UpgradeFrom $Root
        if ($LASTEXITCODE -ne 0) { throw '升级安装失败，原备份已保留' }
    }
    'restore' {
        if (-not $BackupPath) { throw '请使用 -BackupPath 填写备份目录' }
        Restore-ComWorker $BackupPath
    }
    'rollback' {
        if (-not $BackupPath) { throw '请使用 -BackupPath 填写升级前备份目录' }
        $backup = (Resolve-Path -LiteralPath $BackupPath).Path
        $oldRoot = [string](Get-Content -Raw -LiteralPath (Join-Path $backup 'backup-manifest.json') | ConvertFrom-Json).install_dir
        if (-not (Test-Path -LiteralPath (Join-Path $oldRoot 'compose.yml'))) { throw '备份记录的旧版本目录不可用' }
        Restore-ComWorker $backup -NoStart
        $oldCompose = @('compose', '-p', 'comworker-offline', '--env-file', (Join-Path $oldRoot '.env'), '-f', (Join-Path $oldRoot 'compose.yml'))
        $env:ADMIN_PASSWORD = Get-Content -Raw -LiteralPath (Join-Path $oldRoot 'secrets/admin_password')
        Invoke-Docker (@($oldCompose) + @('up', '-d'))
        Write-Host "回滚完成，已启动旧版本目录: $oldRoot"
    }
    'uninstall' {
        if ($Purge) {
            $answer = Read-Host '这将永久删除数据库和所有用户数据。请输入 DELETE 确认'
            if ($answer -ne 'DELETE') { throw '已取消' }
            Invoke-Docker (@($Compose) + @('down', '-v', '--remove-orphans'))
        } else {
            Invoke-Docker (@($Compose) + @('down', '--remove-orphans'))
            Write-Host '容器已移除，数据卷和密钥已保留。'
        }
    }
    'reset-admin-password' {
        if (-not $NewPassword) {
            $bytes = [byte[]]::new(12)
            [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
            $NewPassword = -join ($bytes | ForEach-Object { 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'[$_ % 62] })
        }
        $gwId = (& docker @Compose ps -q gateway).Trim()
        if (-not $gwId) { throw '请先启动 ComWorker（.\comworker.ps1 start）再重置密码' }
        $pyLines = @(
            'import asyncio, os',
            'from app.db.engine import async_session',
            'from app.db.models import User',
            'from app.auth.service import hash_password',
            'from sqlalchemy import select',
            'async def main():',
            '    npw = os.environ["NEW_PW"]',
            '    async with async_session() as s:',
            '        u = (await s.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()',
            '        if not u:',
            '            print("ERROR: 未找到 admin 账户"); raise SystemExit(1)',
            '        u.password_hash = hash_password(npw)',
            '        u.must_change_password = True',
            '        await s.commit()',
            '    print("管理员密码已重置。")',
            'asyncio.run(main())'
        )
        $py = $pyLines -join "`n"
        $env:NEW_PW = $NewPassword
        try {
            Invoke-Docker (@($Compose) + @('exec', '-T', '-e', 'NEW_PW', 'gateway', 'python', '-c', $py))
        } finally {
            Remove-Item Env:NEW_PW -ErrorAction SilentlyContinue
        }
        [IO.File]::WriteAllText((Join-Path $Root 'secrets/admin_password'), $NewPassword)
        Write-Host "管理员密码已重置并保存到 secrets/admin_password: $NewPassword"
    }
}
