# Запуск «Онлайн-деканата» одной командой (Windows). Нужен только Docker Desktop.
#
#   start.cmd                      двойной клик: собрать (если надо), запустить и открыть браузер
#   .\start.ps1 stop | logs | status | rebuild
#
# Порт и прочие настройки берутся из .env (образец — .env.example).

param([string]$Command = "up")
# ErrorActionPreference = Stop тут не ставим: Windows PowerShell 5.1 тогда падает на любом выводе docker в stderr
Set-Location -Path $PSScriptRoot

$port = "8000"
if (Test-Path ".env") {
    $line = Get-Content ".env" | Where-Object { $_ -match '^PORT=' } | Select-Object -Last 1
    if ($line) { $port = $line.Split("=", 2)[1].Trim() }
}
$url = "http://localhost:$port/"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Не найден Docker. Поставьте Docker Desktop: https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop установлен, но не запущен. Запустите его, дождитесь зелёного статуса и повторите." -ForegroundColor Red
    exit 1
}

function Test-Health {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop "$($url)api/v1/health" | Out-Null; return $true } catch { return $false }
}

switch ($Command) {
    "stop" { docker compose down; exit $LASTEXITCODE }
    "logs" { docker compose logs --tail 80 -f; exit $LASTEXITCODE }
    "status" {
        docker compose ps
        if (Test-Health) { Write-Host "сервис отвечает: $url" } else { Write-Host "сервис не отвечает" }
        exit 0
    }
    "rebuild" { docker compose build --pull; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
    "up" { }
    default { Write-Host "неизвестная команда: $Command (есть: up, stop, logs, status, rebuild)"; exit 2 }
}

# мини-LLM для помощника качаем один раз (~1 ГБ), если в .env не выключили UMIR_LLM=0
$llmFile = Join-Path $PSScriptRoot "models\llm\qwen2.5-1.5b-instruct-q4_k_m.gguf"
$llmUrl = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
New-Item -ItemType Directory -Force (Split-Path $llmFile) | Out-Null
$llmOff = (Test-Path (Join-Path $PSScriptRoot ".env")) -and (Select-String -Quiet -Pattern '^UMIR_LLM=(0|false|no|off)' (Join-Path $PSScriptRoot ".env"))
if (-not (Test-Path $llmFile) -and -not $llmOff) {
    Write-Host "Качаю мини-LLM для помощника (~1 ГБ, один раз)..."
    # curl.exe есть в любой Windows 10/11 и качает в разы быстрее Invoke-WebRequest
    curl.exe -fL --progress-bar -o "$llmFile.part" $llmUrl
    if ($LASTEXITCODE -eq 0) { Move-Item -Force "$llmFile.part" $llmFile }
    else {
        Remove-Item -ErrorAction SilentlyContinue "$llmFile.part"
        Write-Host "Модель не скачалась, помощник пока поработает без неё. Повторите start.cmd позже." -ForegroundColor Yellow
    }
}

Write-Host "Запускаю (первая сборка займёт несколько минут)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Write-Host "docker compose завершился с ошибкой" -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host -NoNewline "Жду, пока сервис поднимется"
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Health) {
        Write-Host ""
        Write-Host "Готово: $url" -ForegroundColor Green
        try { Start-Process $url } catch { Write-Host "Откройте в браузере: $url" }
        exit 0
    }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 2
}
Write-Host ""
Write-Host "Сервис не ответил за 2 минуты. Лог:" -ForegroundColor Red
docker compose logs --tail 60
exit 1
