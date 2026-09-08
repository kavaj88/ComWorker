param(
    [string]$UpgradeFrom,
    [switch]$AdoptExisting
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Compose = @('compose', '-p', 'comworker-offline', '-f', 'compose.yml')
$StateFile = Join-Path $Root '.comworker-install.json'
$DatabaseVolume = 'comworker-offline-postgres-data'

function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker 命令失败: docker $($Arguments -join ' ')" }
}

function Write-Utf8NoBom([string]$Path, [string[]]$Lines) {
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllLines($Path, $Lines, $encoding)
}

function New-RandomSecret {
    $value = & docker run --rm --entrypoint sh comworker-postgres:16-alpine -c 'head -c 48 /dev/urandom | base64 | tr -d "\n"'
    if ($LASTEXITCODE -ne 0 -or -not $value) { throw '无法生成本地密钥' }
    return (($value -join '').Trim()).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

function Get-ManifestImageId([string]$Reference) {
    $item = $Manifest.images | Where-Object { $_.ref -eq $Reference } | Select-Object -First 1
    if (-not $item -or -not $item.id) { throw "manifest.json 缺少镜像记录: $Reference" }
    return [string]$item.id
}

function Get-LocalImageId([string]$Reference) {
    $value = & docker image inspect --format '{{.Id}}' $Reference 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return ($value -join '').Trim()
}

Get-Command docker -ErrorAction Stop | Out-Null
Invoke-Docker info | Out-Null
Invoke-Docker compose version | Out-Null
$dockerOs = (& docker info --format '{{.OSType}}').Trim()
if ($dockerOs -ne 'linux') { throw '请将 Docker Desktop 切换到 Linux containers 模式' }
$dockerArch = (& docker info --format '{{.Architecture}}').Trim()
if ($dockerArch -notin @('x86_64', 'amd64')) { throw '当前离线包仅支持 amd64/x86_64' }

foreach ($required in @('manifest.json', 'checksums.sha256', 'images/comworker-images.tar.zst')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $required) -PathType Leaf)) {
        throw "安装包缺少文件: $required"
    }
}

Write-Host '==> 校验安装包'
$checksumCount = 0
foreach ($line in Get-Content -LiteralPath (Join-Path $Root 'checksums.sha256')) {
    if ($line -notmatch '^([0-9a-fA-F]{64})\s+\*?(.+)$') { throw "校验清单格式错误: $line" }
    $checksumCount++
    $expected = $Matches[1].ToLowerInvariant()
    $relativePath = $Matches[2].Replace('/', [IO.Path]::DirectorySeparatorChar)
    if ($relativePath.Split([IO.Path]::DirectorySeparatorChar) -contains '..') { throw "校验清单含不安全路径: $relativePath" }
    $path = Join-Path $Root $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "安装包缺少文件: $relativePath" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "文件校验失败: $relativePath" }
}
if ($checksumCount -eq 0) { throw '校验清单为空' }

$Manifest = Get-Content -Raw -LiteralPath (Join-Path $Root 'manifest.json') | ConvertFrom-Json
if (-not $Manifest.version) { throw 'manifest.json 中缺少版本号' }
$version = [string]$Manifest.version
$images = @(
    "comworker-gateway:$version",
    "comworker-manage:$version",
    "comworker-cca-front:$version",
    "comworker-hermes-agent:$version",
    'comworker-postgres:16-alpine'
)

$needLoad = $false
foreach ($image in $images) {
    $expectedId = Get-ManifestImageId $image
    $localId = Get-LocalImageId $image
    if ($localId -ne $expectedId) { $needLoad = $true }
}
if ($needLoad) {
    Write-Host '==> 导入预置镜像（耗时取决于磁盘速度）'
    & docker load -i (Join-Path $Root 'images/comworker-images.tar.zst') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker 镜像导入失败' }
} else {
    Write-Host '==> 本地镜像与安装包一致，跳过导入'
}
foreach ($image in $images) {
    $expectedId = Get-ManifestImageId $image
    $localId = Get-LocalImageId $image
    if ($localId -ne $expectedId) { throw "镜像导入后 ID 仍不一致: $image" }
}

$archiveLength = (Get-Item -LiteralPath (Join-Path $Root 'images/comworker-images.tar.zst')).Length
$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($Root))
if ($drive.AvailableFreeSpace -lt ($archiveLength * 2)) {
    throw '磁盘空间不足，至少需要镜像归档大小的两倍可用空间'
}

$validState = $false
if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
    try {
        $state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        $validState = $state.product -eq 'ComWorker'
    } catch {
        $validState = $false
    }
}

if ($UpgradeFrom) {
    $oldRoot = (Resolve-Path -LiteralPath $UpgradeFrom -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath (Join-Path $oldRoot '.env') -PathType Leaf)) { throw '旧版本安装目录缺少 .env' }
    if (-not (Test-Path -LiteralPath (Join-Path $oldRoot 'secrets') -PathType Container)) { throw '旧版本安装目录缺少 secrets' }
    if ($oldRoot -ne $Root) {
        Copy-Item -LiteralPath (Join-Path $oldRoot '.env') -Destination (Join-Path $Root '.env') -Force
        New-Item -ItemType Directory -Force -Path (Join-Path $Root 'secrets') | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $oldRoot 'secrets') -Force | Copy-Item -Destination (Join-Path $Root 'secrets') -Recurse -Force
        $oldState = Join-Path $oldRoot '.comworker-install.json'
        if (Test-Path -LiteralPath $oldState -PathType Leaf) { Copy-Item -LiteralPath $oldState -Destination $StateFile -Force }
    }
    $validState = $true
} elseif (-not $validState) {
    & docker volume inspect $DatabaseVolume *> $null
    if ($LASTEXITCODE -eq 0) {
        if (-not $AdoptExisting) {
            throw '检测到已有数据卷但没有安装记录。升级请使用 -UpgradeFrom 旧目录；确认是本机原安装后可使用 -AdoptExisting 接管。'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $Root '.env')) -or -not (Test-Path -LiteralPath (Join-Path $Root 'secrets'))) {
            throw '接管已有数据卷需要当前目录保留 .env 和 secrets'
        }
        $validState = $true
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $Root '.env'))) {
    Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination (Join-Path $Root '.env')
}
$envLines = @(Get-Content -LiteralPath (Join-Path $Root '.env') | Where-Object { $_ -notmatch '^COMWORKER_VERSION=' })
Write-Utf8NoBom (Join-Path $Root '.env') $envLines

New-Item -ItemType Directory -Force -Path (Join-Path $Root 'secrets') | Out-Null
foreach ($name in @('jwt_secret', 'model_keys_master_key', 'admin_password', 'hermes_api_key')) {
    $path = Join-Path (Join-Path $Root 'secrets') $name
    if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -eq 0) {
        [System.IO.File]::WriteAllText($path, (New-RandomSecret), [System.Text.UTF8Encoding]::new($false))
    }
}
$env:ADMIN_PASSWORD = Get-Content -Raw -LiteralPath (Join-Path $Root 'secrets/admin_password')

$passwordLine = $envLines | Where-Object { $_ -match '^POSTGRES_PASSWORD=.+$' }
if (-not $passwordLine) {
    $dbPassword = New-RandomSecret
    $replaced = $false
    $newLines = foreach ($line in $envLines) {
        if ($line -match '^POSTGRES_PASSWORD=') {
            "POSTGRES_PASSWORD=$dbPassword"
            $replaced = $true
        } else { $line }
    }
    if (-not $replaced) { $newLines += "POSTGRES_PASSWORD=$dbPassword" }
    $envLines = @($newLines)
    Write-Utf8NoBom (Join-Path $Root '.env') $envLines
}

$settings = @{}
$envLines | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] } }
if (-not $validState -and -not $UpgradeFrom) {
    $clientPort = if ($settings.ContainsKey('COMWORKER_CLIENT_PORT')) { [int]$settings['COMWORKER_CLIENT_PORT'] } else { 3000 }
    $managePort = if ($settings.ContainsKey('COMWORKER_MANAGE_PORT')) { [int]$settings['COMWORKER_MANAGE_PORT'] } else { 3081 }
    foreach ($port in @($clientPort, $managePort)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
            throw "端口 $port 已被占用，请修改 .env"
        }
    }
}

if ($UpgradeFrom -and $env:COMWORKER_BACKUP_DONE -ne '1') {
    Write-Host '==> 备份升级前数据'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'comworker.ps1') -Action backup -SourceDir $oldRoot
    if ($LASTEXITCODE -ne 0) { throw '升级前备份失败，未启动新版本' }
}

Write-Host "==> 启动 ComWorker $version"
Invoke-Docker @Compose config | Out-Null
Invoke-Docker @Compose up -d

function Wait-ServiceHealthy([string]$Service) {
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        $id = (& docker @Compose ps -q $Service).Trim()
        if ($id) {
            $status = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $id).Trim()
            if ($status -eq 'healthy') { return }
            if ($status -eq 'exited') { throw "$Service 容器已退出" }
        }
        Start-Sleep -Seconds 2
    }
    throw "$Service 未能通过健康检查"
}

foreach ($service in @('postgres', 'gateway', 'manage', 'client')) {
    Write-Host "==> 等待 $service 就绪"
    Wait-ServiceHealthy $service
}

$installState = [ordered]@{
    product = 'ComWorker'
    version = $version
    install_dir = $Root
    installed_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    last_backup = $null
}
Write-Utf8NoBom $StateFile @(ConvertTo-Json $installState)

Write-Host ''
Write-Host '安装完成'
Write-Host "客户端: http://localhost:$($settings['COMWORKER_CLIENT_PORT'])"
Write-Host "管理端: http://localhost:$($settings['COMWORKER_MANAGE_PORT'])"
Write-Host "管理员: $($settings['ADMIN_USERNAME'])"
Write-Host "初始密码: $(Get-Content -Raw -LiteralPath (Join-Path $Root 'secrets/admin_password'))"
Write-Host '运维命令: .\comworker.ps1 status|logs|backup|restore|rollback|uninstall'
