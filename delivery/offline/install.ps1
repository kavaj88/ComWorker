$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Compose = @('compose', '-p', 'comworker-offline', '-f', 'compose.yml')

function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker 命令失败: docker $($Arguments -join ' ')" }
}

function New-RandomSecret {
    $value = & docker run --rm --entrypoint sh comworker-postgres:16-alpine -c 'head -c 48 /dev/urandom | base64 | tr -d "\n"'
    if ($LASTEXITCODE -ne 0 -or -not $value) { throw '无法生成本地密钥' }
    return ($value -join '').Trim()
}

Get-Command docker -ErrorAction Stop | Out-Null
Invoke-Docker info | Out-Null
Invoke-Docker compose version | Out-Null
$dockerOs = (& docker info --format '{{.OSType}}').Trim()
if ($dockerOs -ne 'linux') { throw '请将 Docker Desktop切换到 Linux containers 模式' }
$dockerArch = (& docker info --format '{{.Architecture}}').Trim()
if ($dockerArch -notin @('x86_64', 'amd64')) { throw '当前离线包仅支持 amd64/x86_64' }

Write-Host '==> 校验离线包'
Get-Content checksums.sha256 | ForEach-Object {
    if ($_ -match '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
        $expected = $Matches[1].ToLowerInvariant()
        $path = $Matches[2]
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "文件校验失败: $path" }
    }
}

$example = Get-Content .env.example
$version = (($example | Where-Object { $_ -like 'COMWORKER_VERSION=*' }) -split '=', 2)[1]
$images = @(
    "comworker-gateway:$version",
    "comworker-manage:$version",
    "comworker-cca-front:$version",
    "comworker-hermes-agent:$version",
    'comworker-postgres:16-alpine'
)
$needLoad = $false
foreach ($image in $images) {
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) { $needLoad = $true }
}
if ($needLoad) {
    Write-Host '==> 导入预置镜像（首次安装耗时取决于磁盘速度）'
    & docker load -i images/comworker-images.tar.zst
    if ($LASTEXITCODE -ne 0) { throw 'Docker 镜像导入失败' }
} else {
    Write-Host '==> 镜像已存在，跳过导入'
}

if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force -Path secrets | Out-Null
foreach ($name in @('jwt_secret', 'model_keys_master_key', 'admin_password')) {
    $path = Join-Path secrets $name
    if (-not (Test-Path $path) -or (Get-Item $path).Length -eq 0) {
        Set-Content -LiteralPath $path -NoNewline -Value (New-RandomSecret)
    }
}
$env:ADMIN_PASSWORD = Get-Content -Raw secrets/admin_password

$envLines = Get-Content .env
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
    Set-Content -LiteralPath .env -Value $newLines
}

Write-Host '==> 启动 ComWorker'
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

$settings = @{}
Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] } }
Write-Host ''
Write-Host '安装完成'
Write-Host "客户端: http://localhost:$($settings['COMWORKER_CLIENT_PORT'])"
Write-Host "管理端: http://localhost:$($settings['COMWORKER_MANAGE_PORT'])"
Write-Host "管理员: $($settings['ADMIN_USERNAME'])"
Write-Host "初始密码: $(Get-Content -Raw secrets/admin_password)"
Write-Host '运维命令: .\comworker.ps1 status|logs|backup|uninstall'
