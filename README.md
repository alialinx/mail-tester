# Mail Tester

Self hosted email deliverability tester. You get a disposable address, send a mail to it, and get a score out of 10 with the reason behind every lost point.

Everything runs on your own server. No third party API, no external service holding your mail.

The code has no comments on purpose. Every non obvious decision is explained here.

## What it checks

**Authentication** — SPF is evaluated against the sending IP, not just looked up. DKIM signatures are cryptographically verified, not just found. DMARC is checked for policy and alignment.

**Server reputation** — forward confirmed reverse DNS, HELO name, TLS on delivery, 27 IP blacklists, 4 domain and URL blacklists.

**Content** — SpamAssassin with the full rule breakdown, plain text alternative, text to HTML ratio, image ALT text, shortened links, unsubscribe link, subject line.

## How it works

```
sender  ──25──>  postfix  ──lmtp──>  ingest  ──>  mongo + gridfs
                    │                                  │
                    └── redis (recipient check)         │
                                                        v
browser  <────  api  <────  worker  <────  unbound + spamassassin
```

1. `/generate` creates a random address, stores it in Mongo and writes a key to Redis
2. Postfix asks Redis whether the recipient exists before accepting the mail
3. Accepted mail goes over LMTP to the ingest service, which saves the raw bytes to GridFS
4. `/check` queues the analysis, the worker runs every check and writes the report
5. The browser polls `/check` until the report is ready

The address lives for 30 minutes and can be reused. Send another mail to the same address and check again to get the newest report.

## Services

| Service | Job |
|---|---|
| `mx` | Postfix, the only port open to the internet |
| `ingest` | aiosmtpd LMTP receiver plus the Postfix recipient table |
| `api` | FastAPI, serves the endpoints and the web interface |
| `worker` | Celery, runs the analysis |
| `dns` | unbound, own recursive resolver |
| `spamassassin` | spamd |
| `mongo`, `redis` | storage and cache |

One process per container. A crashing analysis never takes down mail reception, and Postfix restarts do not touch the queue.

## Why a private resolver

Blacklists refuse queries coming from public resolvers like 8.8.8.8. Through Google DNS every list answers `127.255.255.x`, which means *refused*, not *listed* — read naively that is a false positive on every single mail. unbound resolves recursively from the root servers, so the answers are real.

SPF and DKIM verification are wired to the same resolver, so every DNS answer in a report comes from one place.

## Postfix inside Docker

Three settings needed only because Postfix runs in a container:

**`postconf -F '*/*/chroot=n'`** — Debian chroots `smtpd` and `lmtp` into `/var/spool/postfix`, where there is no `resolv.conf`. Without this every recipient lookup fails with `451 Server configuration error`.

**`lmtp_host_lookup = native`** — Postfix does its own DNS and ignores `/etc/hosts`, so Docker service names never resolve. `native` makes it use the system resolver.

**`inet_protocols = ipv4`** — Docker networks usually have no IPv6, and Postfix would otherwise wait on AAAA lookups that never answer.

Two more worth knowing: `default_transport = discard` means nothing is ever delivered outward, so no bounces and no outbound port 25 needed. `virtual_mailbox_maps = static:all` accepts every recipient that already passed the Redis check.

`envsubst` in the entrypoint is called with an explicit variable list. Bare `envsubst` would also expand Postfix's own `$parameters` and blank them.

## Requirements

Docker and Docker Compose. A server with port 25 reachable from the internet, a domain whose MX points at it, and a PTR record for the IP. 2 GB RAM is enough.

## DNS records

```
example.com.      MX    10 mx.example.com.
mx.example.com.   A     203.0.113.10
example.com.      TXT   "v=spf1 a mx ~all"
```

The PTR record for the IP must resolve back to `mx.example.com`. Ask your provider for it.

## Quickstart

```bash
git clone https://github.com/alialinx/mail-tester.git
cd mail-tester
cp .env.example .env
```

Edit `.env`, then:

```bash
docker compose up -d --build
docker compose logs -f mx ingest worker
```

Create the Mongo indexes once:

```javascript
db.test_emails.createIndex({ to_address: 1 }, { unique: true })
db.test_emails.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })
db.mail_events.createIndex({ to_address: 1, _id: -1 })
db.mail_events.createIndex({ created_ip: 1, analyzed_at: 1 })
db.users.createIndex({ email: 1 }, { unique: true })
```

The `expires_at` TTL index deletes the address record but never the report. Reports stay readable after the address is gone.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `DOMAIN` | — | the domain your MX points at |
| `SECRET_KEY` | — | JWT signing key, generate a long random one |
| `MONGODB_URI` | — | full Mongo connection string |
| `DNS_RESOLVER` | `dns` | resolver hostname, keep as is to use unbound |
| `TEST_ADDRESS_TTL_MINUTES` | `30` | how long an address stays alive |
| `ANON_DAILY_LIMIT` | `5` | analyses per IP per day |
| `USER_DAILY_LIMIT` | `25` | analyses per account per day |
| `SPAM_PENALTY_CAP` | `5.0` | most points SpamAssassin alone can cost |
| `DNSBL_MAX_LISTS` | `20` | IP blacklists queried per mail |
| `URIBL_MAX_DOMAINS` | `10` | domains from the body checked against URI lists |

See `.env.example` for the rest.

## Web interface

Put your static files anywhere and point `WEB_ROOT` at them:

```yaml
environment:
  - WEB_ROOT=/app/public
volumes:
  - ./public:/app/public:ro
```

The API serves them from `/` and refuses dotfiles and `.md`, `.yml`, `.log`, `.sql` and similar. Fonts get a one year immutable cache header. If `WEB_ROOT` has no `index.html`, only the JSON API is served.

## Behind an existing reverse proxy

`docker-compose.yml` runs standalone and binds the API to `127.0.0.1:8000`. If you already have Traefik on the server, add the override:

```bash
docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d
```

That file only adds labels and joins an external `edge` network. Ignore it if you use nginx, Caddy or nothing at all.

## Limits

Rate limiting is per IP in Redis and fails open — if Redis is down requests are allowed rather than blocked.

The daily quota is charged when the analysis starts, not when the address is created, so generating an address or switching language costs nothing. A mail blocked by the quota is retried automatically once the quota frees up.

## Scoring

Every mail starts at 10 and loses points:

| Problem | Cost |
|---|---|
| No SPF record | 2.0 |
| SPF returns fail | 2.0 |
| No DKIM signature, or it does not verify | 1.5 |
| No DMARC record, or alignment fails | 1.0 |
| Listed on IP blacklists | 0.4 each, max 2.0 |
| Listed on domain blacklists | 1.0 each, max 2.0 |
| No TLS on delivery | 0.5 |
| Reverse DNS missing or not forward confirmed | 0.5 |
| Missing Message-ID or Date | 0.5 each |
| SpamAssassin content rules | their own points, max `SPAM_PENALTY_CAP` |

SpamAssassin rules that repeat a check already made — SPF, DKIM, DMARC, blacklists, missing headers, all caps subject, image only body — are excluded so nothing is punished twice.

9 and above is excellent, 7 good, 5 average, below that likely to be filtered.

## Endpoints

| Endpoint | Job |
|---|---|
| `POST /generate` | new test address |
| `GET /check/{address}` | queue the analysis and poll for it |
| `GET /result/{address}` | newest report, read only, never touches the quota |
| `GET /limits` | remaining quota |
| `POST /register`, `POST /login`, `GET /me`, `POST /logout` | accounts |

`/check` returns one of `waiting`, `processing`, `analyzed`, `limit`, `expired`, `error`. Pass `?after=<event_id>` to ignore mails you have already seen.

## Testing without real mail

```bash
python - <<'EOF'
import smtplib
from email.message import EmailMessage
m = EmailMessage()
m["From"] = "test@example.com"
m["To"] = "test-xxxx@yourdomain.com"
m["Subject"] = "test"
m.set_content("hello")
s = smtplib.SMTP("localhost", 25)
s.send_message(m)
s.quit()
EOF
```

Watch it land with `docker compose logs -f mx ingest worker`.
