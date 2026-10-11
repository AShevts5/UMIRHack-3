#!/usr/bin/env bash
# Запуск «Онлайн-деканата» одной командой (Linux/macOS). Нужен только Docker.
#
#   ./start.sh            # собрать (если надо) и запустить, потом открыть браузер
#   ./start.sh stop       # остановить (данные пользователей остаются в томе)
#   ./start.sh logs       # хвост лога
#   ./start.sh status     # жив ли сервис
#   ./start.sh rebuild    # пересобрать образ после обновления кода
#
# Порт и прочие настройки берутся из .env (образец — .env.example).

set -euo pipefail
cd "$(dirname "$0")"

PORT="$(grep -E '^PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 || true)"
PORT="${PORT:-8000}"
URL="http://localhost:$PORT/"

command -v docker >/dev/null 2>&1 || { echo "Не найден Docker. Поставьте Docker Desktop или Docker Engine: https://docs.docker.com/get-docker/" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker установлен, но не запущен. Запустите его и повторите." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Нужен Docker Compose v2 (команда «docker compose»)." >&2; exit 1; }

case "${1:-up}" in
  stop) docker compose down; exit 0 ;;
  logs) docker compose logs --tail 80 -f; exit 0 ;;
  status) docker compose ps; curl -fsS "${URL}api/v1/health" && echo || echo "сервис не отвечает"; exit 0 ;;
  rebuild) docker compose build --pull ;;
  up) ;;
  *) echo "неизвестная команда: $1 (есть: up, stop, logs, status, rebuild)" >&2; exit 2 ;;
esac

echo "Запускаю (первая сборка займёт несколько минут)…"
docker compose up -d --build

echo -n "Жду, пока сервис поднимется"
for _ in $(seq 1 60); do
  if curl -fsS "${URL}api/v1/health" >/dev/null 2>&1; then
    echo; echo "Готово: $URL"
    if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then open "$URL" || true; fi
    exit 0
  fi
  echo -n "."; sleep 2
done
echo; echo "Сервис не ответил за 2 минуты. Лог:" >&2
docker compose logs --tail 60 >&2
exit 1
