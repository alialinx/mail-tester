#!/bin/sh
set -e

if [ -z "$MAIL_DOMAIN" ]; then
    echo "MAIL_DOMAIN tanımlı değil, .env dosyasını kontrol et" >&2
    exit 1
fi

export MAIL_DOMAIN
export MX_HOSTNAME="${MX_HOSTNAME:-mx.$MAIL_DOMAIN}"
export INGEST_HOST="${INGEST_HOST:-ingest}"
export INGEST_LMTP_PORT="${INGEST_LMTP_PORT:-2400}"
export INGEST_MAP_PORT="${INGEST_MAP_PORT:-2500}"
export MESSAGE_SIZE_LIMIT="${MESSAGE_SIZE_LIMIT:-26214400}"
export TLS_CERT_FILE="${TLS_CERT_FILE:-/etc/postfix/tls/mx.crt}"
export TLS_KEY_FILE="${TLS_KEY_FILE:-/etc/postfix/tls/mx.key}"

if [ ! -f "$TLS_CERT_FILE" ] || [ ! -f "$TLS_KEY_FILE" ]; then
    echo "TLS sertifikası bulunamadı, kendi imzalı sertifika üretiliyor"
    mkdir -p "$(dirname "$TLS_CERT_FILE")"
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -subj "/CN=$MX_HOSTNAME" \
        -keyout "$TLS_KEY_FILE" -out "$TLS_CERT_FILE" 2>/dev/null
    chmod 600 "$TLS_KEY_FILE"
fi

envsubst '$MAIL_DOMAIN $MX_HOSTNAME $INGEST_HOST $INGEST_LMTP_PORT $INGEST_MAP_PORT $MESSAGE_SIZE_LIMIT $TLS_CERT_FILE $TLS_KEY_FILE' \
    < /etc/mailtester/main.cf.template > /etc/postfix/main.cf

postconf -F '*/*/chroot=n'

if ! postfix -c /etc/postfix check 2>&1; then
    echo "postfix check başarısız, üretilen config:" >&2
    cat /etc/postfix/main.cf >&2
    exit 1
fi

echo "mx başlıyor: $MX_HOSTNAME ($MAIL_DOMAIN) -> lmtp:$INGEST_HOST:$INGEST_LMTP_PORT"

exec /usr/sbin/postfix start-fg
