#!/usr/bin/env bash
# Деплой и рулёжка «Онлайн-деканатом» на сервере через Docker.
#
#   bash scripts/deploy_remote.sh                 # развернуть или обновить (код + образ + запуск)
#   bash scripts/deploy_remote.sh deploy --no-build   # только перезапустить на текущем образе
#   bash scripts/deploy_remote.sh status|logs|restart|stop|start
#
# Скрипт идемпотентный: гоняй deploy сколько хочешь — он заберёт свежий код и перезапустит сервис.
# Всё сидит в одном контейнере (OCR + FastAPI + собранный интерфейс), соседние
# сервисы на машине мы не трогаем.
#
# Сервер у нас слабенький (1 ядро, ~960 МБ памяти, рядом ещё сервисы), поэтому память и CPU
# контейнера урезаем, чтобы OCR не сожрал всю машину. Порт задаётся переменной PORT.
#
# Важно: разворачивается то, что лежит на GitHub в ветке BRANCH, а не локальная папка.
# Сначала запушь изменения, потом деплой. Для запуска на своём компе есть start.cmd / start.sh.
#
# Пользователи, история проверок и ключ подписи входа живут в DATA_DIR на хосте (монтируется
# в /app/var), так что передеплой их больше не стирает. Настройки (демо-аккаунт, ключи Яндекса,
# UMIR_PUBLIC_URL) кладём в ENV_FILE по образцу .env.example. Оба пути специально лежат вне
# APP_DIR: там git clean, и всё лишнее он сносит.
#
# Мини-LLM для AI-помощника (Qwen2.5-1.5B, ~1 ГБ на диске и ~1.5 ГБ в памяти) на нашем
# сервере по умолчанию не включаем: на одном ядре и 960 МБ она не влезет. На машине
# помощнее: LLM=1 bash scripts/deploy_remote.sh — модель скачается в MODELS_DIR (тоже вне
# APP_DIR), подключится в контейнер, а памяти контейнеру дадим 3g.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/umir-online-dean}"
REPO_URL="${REPO_URL:-https://github.com/AShevts5/UMIRHack-3.git}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-8000}"
IMAGE="${IMAGE:-umir-dean:latest}"
CONTAINER="${CONTAINER:-umir-dean}"
LLM="${LLM:-0}"
if [ "$LLM" = "1" ]; then MEMORY="${MEMORY:-3g}"; else MEMORY="${MEMORY:-700m}"; fi
MEMORY_SWAP="${MEMORY_SWAP:-$([ "$LLM" = "1" ] && echo "$MEMORY" || echo 1g)}"
MODELS_DIR="${MODELS_DIR:-/opt/umir-online-dean-models}"
LLM_FILE="qwen2.5-1.5b-instruct-q4_k_m.gguf"
LLM_URL="${LLM_URL:-https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/$LLM_FILE}"
CPUS="${CPUS:-0.9}"
DATA_DIR="${DATA_DIR:-/opt/umir-online-dean-data}"
ENV_FILE="${ENV_FILE:-/opt/umir-online-dean.env}"

COMMAND="${1:-deploy}"
shift || true
NO_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --no-build) NO_BUILD=1 ;;
    *) echo "неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\n=== %s ===\n' "$*"; }
url() { echo "http://$(hostname -I | awk '{print $1}'):$PORT/"; }

case "$COMMAND" in
  status)
    docker ps --filter "name=^/${CONTAINER}$" --format 'состояние: {{.Status}} | образ: {{.Image}} | порт: {{.Ports}}'
    echo -n "health: "
    curl -fsS "http://127.0.0.1:$PORT/api/v1/health" || echo "не отвечает"
    echo
    echo "память контейнера:"
    docker stats --no-stream --format '  {{.MemUsage}} ({{.MemPerc}}) CPU {{.CPUPerc}}' "$CONTAINER" 2>/dev/null || true
    echo "ссылка: $(url)"
    exit 0
    ;;
  logs)
    docker logs --tail "${LINES:-80}" -f "$CONTAINER"
    exit 0
    ;;
  restart)
    docker restart "$CONTAINER" >/dev/null
    echo "перезапущен: $(url)"
    exit 0
    ;;
  stop)
    docker stop "$CONTAINER" >/dev/null
    echo "остановлен"
    exit 0
    ;;
  start)
    docker start "$CONTAINER" >/dev/null
    echo "запущен: $(url)"
    exit 0
    ;;
  deploy) ;;
  *)
    echo "неизвестная команда: $COMMAND (доступны deploy, status, logs, restart, stop, start)" >&2
    exit 2
    ;;
esac

log "1/6 Проверяю Docker"
command -v docker >/dev/null 2>&1 || { echo "Docker не установлен" >&2; exit 1; }
docker --version

log "2/6 Забираю код (ветка $BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  # Папка может быть «грязной»: сюда могли накидать файлов руками (например,
  # rules.md ещё до того, как он появился в репозитории). Без чистки git откажется
  # переключаться. Репозиторий тут чисто под деплой, так что чистим без сожалений.
  git -C "$APP_DIR" clean -fdq
  git -C "$APP_DIR" reset -q --hard
  git -C "$APP_DIR" checkout -q FETCH_HEAD
  git -C "$APP_DIR" reset -q --hard FETCH_HEAD
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
echo "коммит: $(git -C "$APP_DIR" rev-parse --short HEAD) — $(git -C "$APP_DIR" log -1 --format=%s)"

# Положение (rules.md) — единственный источник правил в кейсе. В образ его не
# вшиваем: файл читает раздел «Положение», а подключаем его монтированием, так
# что поправили текст — и пересобирать ничего не надо.
RULES_PATH="$APP_DIR/rules.md"
if [ ! -s "$RULES_PATH" ]; then
  log "2.1 Забираю rules.md (Положение)"
  RULES_URL="${RULES_URL:-https://raw.githubusercontent.com/AShevts5/UMIRHack-3/$BRANCH/rules.md}"
  if ! curl -fsS "$RULES_URL" -o "$RULES_PATH"; then
    RULES_ARCHIVE="${RULES_ARCHIVE:-https://raw.githubusercontent.com/AShevts5/UMIRHack-3/$BRANCH/online-dean-participants-v1/rules.md}"
    curl -fsS "$RULES_ARCHIVE" -o "$RULES_PATH" || {
      echo "не удалось скачать rules.md, раздел «Положение» будет пустым" >&2
      rm -f "$RULES_PATH"
    }
  fi
  [ -s "$RULES_PATH" ] && echo "rules.md: $(wc -c <"$RULES_PATH") байт"
fi

if [ "$LLM" = "1" ] && [ ! -s "$MODELS_DIR/llm/$LLM_FILE" ]; then
  log "2.2 Качаю мини-LLM для помощника (~1 ГБ, один раз)"
  mkdir -p "$MODELS_DIR/llm"
  if curl -fL --progress-bar -o "$MODELS_DIR/llm/$LLM_FILE.part" "$LLM_URL"; then
    mv "$MODELS_DIR/llm/$LLM_FILE.part" "$MODELS_DIR/llm/$LLM_FILE"
  else
    rm -f "$MODELS_DIR/llm/$LLM_FILE.part"
    echo "модель не скачалась, помощник будет отвечать без неё" >&2
  fi
fi

if [ "$NO_BUILD" -eq 0 ]; then
  log "3/6 Собираю образ (самая долгая часть, несколько минут)"
  export DOCKER_BUILDKIT=1
  docker build -t "$IMAGE" "$APP_DIR"
else
  log "3/6 Сборка пропущена (--no-build)"
fi

log "4/6 Останавливаю прежний контейнер"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

log "5/6 Запускаю контейнер (память $MEMORY, CPU $CPUS, порт $PORT)"
mkdir -p "$DATA_DIR"
MOUNTS=(-v "$DATA_DIR:/app/var" -e UMIR_DATA_DIR=/app/var)
if [ -s "$APP_DIR/rules.md" ]; then
  MOUNTS+=(-v "$APP_DIR/rules.md:/app/rules.md:ro")
fi
if [ "$LLM" = "1" ] && [ -s "$MODELS_DIR/llm/$LLM_FILE" ]; then
  MOUNTS+=(-v "$MODELS_DIR/llm:/app/models/llm:ro")
  echo "мини-LLM: $MODELS_DIR/llm/$LLM_FILE"
fi
if [ -f "$ENV_FILE" ]; then
  MOUNTS+=(--env-file "$ENV_FILE")
  echo "настройки: $ENV_FILE"
fi
echo "данные: $DATA_DIR"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --memory "$MEMORY" --memory-swap "$MEMORY_SWAP" \
  --cpus "$CPUS" \
  -p "$PORT:8000" \
  -e UMIR_OCR_LANGS=rus+eng \
  -e OMP_THREAD_LIMIT=1 \
  "${MOUNTS[@]}" \
  "$IMAGE" >/dev/null

log "6/6 Жду готовности сервиса"
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/api/v1/health" -o /tmp/umir-health.json 2>/dev/null; then
    echo "health:"; head -c 300 /tmp/umir-health.json; echo; echo
    curl -fsS -o /dev/null -w 'document-types -> HTTP %{http_code}\n' "http://127.0.0.1:$PORT/api/v1/document-types" || true
    curl -fsS "http://127.0.0.1:$PORT/api/regulation" 2>/dev/null \
      | head -c 120 | sed 's/^/regulation: /' || true
    echo
    echo "Готово. Ссылка: $(url)"
    exit 0
  fi
  sleep 3
done

echo "Сервис не поднялся за 2 минуты. Последние строки лога:" >&2
docker logs --tail 60 "$CONTAINER" >&2 || true
exit 1
