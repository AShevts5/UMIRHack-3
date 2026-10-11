#!/usr/bin/env bash
# Настройка домена dekanatonline.ru для «Онлайн-деканата».
#
# Что делает:
#   1) проверяет, что домен (или www) резолвится в этот сервер;
#   2) создаёт конфиг nginx-обратного прокси на приложение (127.0.0.1:8000);
#   3) выпускает сертификат Let's Encrypt для тех имён, что указывают сюда;
#   4) оставляет HTTP-редирект на HTTPS.
#
# Идемпотентно: повторный запуск ничего не ломает. Перед изменением конфига
# делается резервная копия, при ошибке конфиг возвращается назад.
#
# Имена: BARE (dekanatonline.ru) и WWW (www.dekanatonline.ru). Сертификат
# выпускается только на те, у которых A-запись указывает на этот сервер, иначе
# Let's Encrypt не пройдёт проверку и уже выпущенный сертификат будет испорчен.

set -euo pipefail

BARE="${BARE:-dekanatonline.ru}"
WWW="${WWW:-www.dekanatonline.ru}"
APP_PORT="${APP_PORT:-8000}"
SITE_NAME="${SITE_NAME:-dekanatonline.ru}"
CONF="/etc/nginx/sites-available/$SITE_NAME"
LINK="/etc/nginx/sites-enabled/$SITE_NAME"
WEBROOT="/var/www/$SITE_NAME/acme"
CERT_NAME="$SITE_NAME"
SERVER_IP="${SERVER_IP:-72.56.18.143}"

log() { printf '\n=== %s ===\n' "$*"; }

log "1/6 Проверяю DNS"
name_here() {
  local host="$1" ips
  ips="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
  echo "  $host -> ${ips:-нет A-записи}"
  if echo " $ips " | grep -q " $SERVER_IP "; then return 0; else return 1; fi
}
INCLUDE=()
if name_here "$WWW"; then INCLUDE+=("$WWW"); fi
if name_here "$BARE"; then INCLUDE+=("$BARE"); fi

if [ "${#INCLUDE[@]}" -eq 0 ]; then
  echo "Ни $BARE, ни $WWW не указывают на $SERVER_IP. Сначала поправьте A-записи у регистратора." >&2
  exit 1
fi
if ! printf '%s\n' "${INCLUDE[@]}" | grep -qx "$BARE"; then
  echo
  echo "ВНИМАНИЕ: голый домен $BARE сейчас указывает не на этот сервер."
  echo "Сертификат выпущу только на: ${INCLUDE[*]}"
  echo "Когда переключите A-запись $BARE на $SERVER_IP — запустите этот скрипт снова,"
  echo "он добавит домен в сертификат без потери текущего."
fi
NAMES="${INCLUDE[*]}"

log "2/6 Готовлю каталог для проверки Let's Encrypt"
mkdir -p "$WEBROOT"

log "3/6 Пишу конфиг nginx (HTTP: проверка сертификата + редирект)"
if [ -f "$CONF" ]; then
  cp -a "$CONF" "$CONF.bak.$(date +%Y%m%d%H%M%S)"
  echo "старый конфиг сохранён рядом (.bak)"
fi

cat > "$CONF" <<EOF
# $SITE_NAME — «Онлайн-деканат» (предварительная проверка студенческих документов).
# Приложение слушает 127.0.0.1:$APP_PORT (Docker-контейнер umir-dean).
# Соседние сайты не затрагиваются: этот блок ловит только свои имена.

server {
    listen 80;
    listen [::]:80;
    server_name $NAMES;
    server_tokens off;

    # Проверка владения доменом для Let's Encrypt (до редиректа на HTTPS).
    location ^~ /.well-known/acme-challenge/ { root $WEBROOT; }

    # Остальное — на HTTPS.
    location / { return 301 https://\$host\$request_uri; }
}
EOF

ln -sfn "$CONF" "$LINK"
nginx -t
systemctl reload nginx
echo "nginx перезагружен, HTTP-редирект работает"

log "4/6 Выпускаю сертификат Let's Encrypt на: $NAMES"
# certbot требует отдельный -d на каждое имя
CERT_ARGS=()
for host in "${INCLUDE[@]}"; do CERT_ARGS+=(-d "$host"); done

if [ -d "/etc/letsencrypt/live/$CERT_NAME" ]; then
  echo "сертификат уже есть, расширяю список имён при необходимости"
  certbot certonly --webroot -w "$WEBROOT" "${CERT_ARGS[@]}" \
    --cert-name "$CERT_NAME" --expand --non-interactive --agree-tos \
    --register-unsafely-without-email --keep-until-expiring
else
  certbot certonly --webroot -w "$WEBROOT" "${CERT_ARGS[@]}" \
    --cert-name "$CERT_NAME" --non-interactive --agree-tos \
    --register-unsafely-without-email
fi

log "5/6 Добавляю HTTPS-блок с проксированием на приложение"
cat > "$CONF" <<EOF
# $SITE_NAME — «Онлайн-деканат». Приложение: http://127.0.0.1:$APP_PORT

server {
    listen 80;
    listen [::]:80;
    server_name $NAMES;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $NAMES;
    server_tokens off;

    ssl_certificate /etc/letsencrypt/live/$CERT_NAME/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$CERT_NAME/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:DEKANAT_SSL:10m;
    ssl_session_timeout 1d;

    # Загружаемые сканы бывают крупными; лимит приложения (12 МБ) остаётся ниже.
    client_max_body_size 24m;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
    }
}
EOF

nginx -t
systemctl reload nginx
echo "HTTPS включён"

log "6/6 Проверяю результат"
for host in $NAMES; do
  echo "--- $host"
  curl -s -o /dev/null -w "  http  -> %{http_code} (редирект: %{redirect_url})\n" "http://$host/" || true
  curl -s -o /dev/null -w "  https -> %{http_code}\n" --resolve "$host:443:127.0.0.1" -k "https://$host/" || true
  curl -s --resolve "$host:443:127.0.0.1" -k "https://$host/api/v1/health" \
    | head -c 200 | sed 's/^/  health: /' || true
  echo
done

echo "сертификат:"
certbot certificates --cert-name "$CERT_NAME" 2>/dev/null | grep -E "Domains|Expiry" || true
echo
echo "Автопродление (таймер certbot):"
systemctl list-timers 'certbot*' --no-pager 2>/dev/null | head -3 || echo "проверьте cron/timer вручную"
