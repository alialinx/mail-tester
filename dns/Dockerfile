FROM alpine:3.20

RUN apk add --no-cache unbound

COPY dns/unbound.conf /etc/unbound/unbound.conf

EXPOSE 53/udp
EXPOSE 53/tcp

CMD ["unbound", "-d", "-c", "/etc/unbound/unbound.conf"]
