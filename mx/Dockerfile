FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# postfix kurulumu interaktif soru soruyor, önceden cevaplıyoruz
RUN echo "postfix postfix/main_mailer_type select No configuration" | debconf-set-selections \
  && apt-get update && apt-get install -y --no-install-recommends \
     postfix openssl gettext-base ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY mx/main.cf.template /etc/postfix/main.cf.template
COPY mx/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25

ENTRYPOINT ["/entrypoint.sh"]
