# Mail Tester

Self hosted email deliverability tester. You get a disposable address, send a mail to it, and get a score out of 10 with the reason behind every lost point.

The code has no comments on purpose. Every non obvious decision is here.

## What it checks

SPF is evaluated against the sending IP, not just looked up. DKIM signatures are cryptographically verified. DMARC is checked for policy and alignment. Plus forward confirmed reverse DNS, HELO name, TLS, 27 IP blacklists, 4 domain and URL blacklists, SpamAssassin with the full rule breakdown, and the content itself: plain text alternative, text to HTML ratio, image ALT, shortened links, unsubscribe link, subject line.

## How it works

```
sender ──25──> postfix ──lmtp──> ingest ──> mongo + gridfs
                 │ redis (recipient check)      │
browser <── api <── worker <── unbound + spamassassin
```

`/generate` creates a random address and writes a key to Redis. Postfix asks Redis whether the recipient exists before accepting. Accepted mail goes over LMTP to ingest, which stores the raw bytes in GridFS. `/check` queues the analysis, the worker writes the report, the browser polls until it is ready.

The address lives 30 minutes and can be reused. Send another mail to it and check again for the newest report.

Containers: `mx` (postfix), `ingest` (aiosmtpd), `api` (fastapi), `worker` (celery), `dns` (unbound), `spamassassin`, `mongo`, `redis`. One process each, so a failing analysis never takes down mail reception.

## Why a private resolver

Blacklists refuse queries from public resolvers like 8.8.8.8. Through Google DNS every list answers `127.255.255.x`, which means *refused*, not *listed* — read naively that is a false positive on every mail. unbound resolves from the root servers, so the answers are real. SPF and DKIM verification use the same resolver.

## Postfix inside Docker

Three settings needed only because Postfix runs in a container:

- **`postconf -F '*/*/chroot=n'`** — Debian chroots `smtpd` and `lmtp` into `/var/spool/postfix`, where there is no `resolv.conf`. Without this every recipient lookup fails with `451 Server configuration error`.
- **`lmtp_host_lookup = native`** — Postfix does its own DNS and ignores `/etc/hosts`, so Docker service names never resolve.
- **`inet_protocols = ipv4`** — Docker networks usually have no IPv6 and Postfix would wait on AAAA lookups that never answer.

`default_transport = discard` means nothing is delivered outward, so no bounces and no outbound port 25 needed. `envsubst` in the entrypoint is called with an explicit variable list, otherwise it would also blank Postfix's own `$parameters`.

Only port 25 needs to be open for mail. 587 and 465 are for authenticated sending and must stay closed.

## Setup

Needs Docker, port 25 reachable, and a domain whose MX points at the server.

```
example.com.      MX    10 mx.example.com.
mx.example.com.   A     203.0.113.10
example.com.      TXT   "v=spf1 a mx ~all"
```

The PTR record for the IP must resolve back to `mx.example.com`.

```bash
git clone https://github.com/alialinx/mail-tester.git
cd mail-tester
cp .env.example .env
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
db.analyses.createIndex({ "owner.user_id": 1, _id: -1 })
db.api_keys.createIndex({ key_hash: 1 }, { unique: true })
db.api_keys.createIndex({ user_id: 1, revoked_at: 1 })
```

The TTL index deletes the address record but never the report, so reports stay readable after the address is gone.

## Environment

`DOMAIN`, `SECRET_KEY` and `MONGODB_URI` are required. The rest have defaults:

| Variable | Default | Meaning |
|---|---|---|
| `DNS_RESOLVER` | `dns` | resolver hostname, keep as is to use unbound |
| `TEST_ADDRESS_TTL_MINUTES` | `30` | how long an address stays alive |
| `ANON_DAILY_LIMIT` | `5` | analyses per IP per day |
| `USER_DAILY_LIMIT` | `25` | analyses per account per day |
| `SPAM_PENALTY_CAP` | `5.0` | most points SpamAssassin alone can cost |
| `WEB_ROOT` | `public` | static files served from `/`, JSON only if empty |

Rate limiting is per IP in Redis and fails open. The daily quota is charged when the analysis starts, not when the address is created, so generating an address costs nothing. A mail blocked by the quota is retried once the quota frees up.

Behind an existing Traefik, add the override: `docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d`. It only adds labels and joins the external `edge` network. Ignore it with nginx, Caddy or nothing.

## Scoring

Every mail starts at 10 and loses points: no SPF record or SPF fail 2.0, DKIM missing or not verifying 1.5, DMARC missing or misaligned 1.0, IP blacklists 0.4 each up to 2.0, domain blacklists 1.0 each up to 2.0, no TLS 0.5, reverse DNS missing or not forward confirmed 0.5, missing Message-ID or Date 0.5 each, SpamAssassin content rules their own points up to `SPAM_PENALTY_CAP`.

SpamAssassin rules that repeat a check already made — SPF, DKIM, DMARC, blacklists, missing headers, all caps subject, image only body — are excluded so nothing is punished twice.

9 and above is excellent, 7 good, 5 average, below that likely filtered.

## Endpoints

| Endpoint | Job |
|---|---|
| `POST /generate` | new test address |
| `GET /check/{address}` | queue the analysis and poll for it |
| `GET /result/{address}` | newest report, read only, never touches the quota |
| `GET /limits` | remaining quota |
| `GET /history`, `GET /history/{id}` | past reports of the signed in account |
| `POST /keys`, `GET /keys`, `DELETE /keys/{id}` | API keys |
| `POST /register`, `POST /login`, `GET /me`, `POST /logout` | accounts |

`/check` returns `waiting`, `processing`, `analyzed`, `limit`, `expired` or `error`. Pass `?after=<event_id>` to ignore mails you have already seen.

Requests authenticate one of three ways. A browser sends the JWT from `/login` as a bearer token. A server sends an `X-API-Key` header, created from an account and stored only as a sha256 hash. Anything else is anonymous and limited per IP.

Each scope has its own daily budget: anonymous by IP, accounts on the user record, API keys on the key record. A key can therefore be raised or throttled on its own without touching the owner.

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
