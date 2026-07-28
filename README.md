# Mail Tester

Mail Tester is a self hosted service that checks the quality and deliverability of outgoing emails.

Users send an email to a temporary test address. The system receives the email on its own SMTP
server, analyzes it in the background and returns a score with explanations.

The code contains no comments on purpose. Every non obvious decision is explained in this file,
so this is the place to look before changing anything.

---

## What This Tool Does

You can test:

- Email server configuration
- SPF, DKIM and DMARC records
- Reverse DNS (rDNS)
- Sending IP blacklist status
- The SMTP connection itself: client IP, HELO name, STARTTLS version and cipher
- Envelope sender (MAIL FROM) and the From header
- Required and recommended email headers
- Basic email content quality
- SpamAssassin score and rules

---

## How It Works

1. The user calls `POST /generate`.
2. The system creates a unique temporary address, for example `test-a92bd12f98bc4@yourdomain.com`,
   stores it in MongoDB and writes it to Redis with a TTL.
3. The user sends an email to that address.
4. `mx` receives it on port 25. For every `RCPT TO` it asks `ingest` whether the address is live.
   Unknown or expired addresses are rejected inside the SMTP conversation, so mail to random
   addresses never enters the queue or touches the disk.
5. `mx` delivers the mail over LMTP to `ingest`, which stores the raw message in GridFS together
   with the connection facts and queues the analysis task.
6. The Celery worker analyzes the mail and stores the result.
7. The browser asks `GET /check/{to_address}` until the analysis is ready.

There is no IMAP polling and no mailbox anywhere.

**The address is reusable.** It stays live for its whole lifetime and accepts more than one message.
Each arriving mail becomes its own `mail_events` document with its own analysis, so a sender can fix
a problem, send again to the same address and compare. `GET /check/{to_address}?after=<event_id>`
only reports mail that arrived after the event the caller already has, which is what makes a "check
again" button able to distinguish "nothing new yet" from "here is the newer message".

The daily quota is charged per analysed message, not per address, and each message can only be
charged once: the worker claims a `mail_events` document with a conditional update on
`analysis_started_at`, so a task delivered twice cannot bill twice.

---

## Architecture

```
internet ──25──▶ mx (Postfix)
                   │
                   ├─ RCPT TO  ──tcp:2500──▶ ingest ──▶ Redis
                   └─ DATA     ──lmtp:2400─▶ ingest
                                               ├─ raw mail  ──▶ MongoDB GridFS
                                               └─ task      ──▶ Redis ──▶ worker
                                                                            │
                                       browser ◀── SSE ── api ◀── MongoDB ──┘
```

| Service | Job | Limits |
|---|---|---|
| `mx` | Postfix. Receives mail on port 25. No mailbox, no alias, no SASL, no relay. | 128m |
| `ingest` | LMTP server plus Postfix recipient map. Stores the mail and triggers analysis. | 256m |
| `api` | FastAPI. Address generation, results, SSE stream, optional web interface. | 512m |
| `worker` | Celery. Runs the analysis. | 1g, 2 cpu |
| `dns` | unbound. Own recursive resolver, required for correct blacklist results. | 128m |
| `spamassassin` | spamd, reached over TCP. | 768m, 2 cpu |
| `mongo` | Test addresses, mail events, analysis results, raw mails (GridFS). | 1g |
| `redis` | Celery broker, live test addresses, SSE pub/sub. | 256m |

### Why one process per container

Each service has a different lifecycle, so they are kept apart on purpose:

- `mx` must hold port 25 and stay up. Deploying API code must not interrupt mail reception, and
  because Postfix queues what it cannot deliver yet, a restart of `ingest` loses nothing.
- `ingest` parses attacker controlled MIME, which is the highest risk code in the project. It runs
  with the smallest possible surface and can be isolated further without touching anything else.
- `worker` is CPU bursty: a single analysis fires dozens of parallel DNS queries and a
  SpamAssassin scan. Its memory and CPU caps keep it from starving neighbours on a shared host.
- `mongo`, `redis`, `spamassassin` and `unbound` are third party images. Folding them into one
  image would mean a process supervisor inside a container, which is the actual anti pattern.

`api` and `worker` share one image and differ only by their command, so the extra container costs
almost nothing and buys independent restart and scaling.

The Python image runs as an unprivileged user. Nothing in the application writes to disk, uploads go
to GridFS and logs go to stdout, so root brings no benefit and `ingest` is the process that parses
attacker controlled input. Only `mx` stays root, because Postfix requires it. All listening ports
are above 1024, so dropping privileges costs nothing.

Every service declares `mem_limit` and log rotation locally instead of relying on the Docker
daemon configuration, so installing this stack never requires restarting the Docker daemon on a
host that runs other projects.

---

## Postfix Configuration Decisions

`mx/main.cf.template` is short but almost every line is there for a reason.

**`virtual_mailbox_maps = static:all`** makes every address in the domain look deliverable, and
the real decision is made by the recipient map. Postfix has to accept the address as listed before
it consults an access map.

**Restriction order.** `reject_unauth_destination` comes first in
`smtpd_recipient_restrictions`, before the recipient map. If the map ever answered `OK` for a
foreign domain because of a bug, the first rule has already refused to relay. Open relay is the
single worst failure mode for a mail server, so it is blocked structurally, not by correctness of
our own code.

**`check_recipient_access tcp:...`** speaks the `tcp_table(5)` protocol. Postfix sends
`get <address>` for every recipient and `ingest` answers `200 OK` or
`200 REJECT test address is unknown or expired`. Values are URL encoded. The lookup hits Redis, not
MongoDB, so it stays inside the SMTP timeout budget.

**`lmtp_host_lookup = native`.** Postfix's LMTP client resolves hosts through DNS only and ignores
`/etc/hosts` and NSS. In Docker that means container names cannot be resolved and delivery fails
with `Host or domain name not found`. `native` switches it to `getaddrinfo`.

**`inet_protocols = ipv4`.** With the default `all`, Postfix looks up an AAAA record for `ingest`,
does not find one, treats it as a permanent error and bounces the mail.

**`default_transport = discard`.** This server must never send mail to the internet. Instead of
generating a bounce for an undeliverable message, it is dropped silently, so the host can never
become a backscatter source. The cost: if `ingest` stays down past the queue lifetime, the sender
is never told, and the user only sees the address expire.

**`maximal_queue_lifetime = 1h`.** Test addresses live for minutes, so keeping mail queued for the
default five days is pointless.

**`smtpd_tls_received_header = yes`.** Off by default. Without it Postfix records only `ESMTPS` in
its `Received` header and the TLS version and cipher are lost, which are exactly the values the
report grades.

**No `mydomain` or `myorigin`.** Their normal values use Postfix's own `$parameter` syntax, which
`envsubst` would blank out while rendering the template. They are unnecessary for a server that
never sends mail, so they are simply absent.

**No DNSBL, postscreen or content filter.** This is deliberate and it is the opposite of a normal
mail server. The job of a mail tester is to tell a blacklisted sender that it is blacklisted; if
we rejected that sender at connect time the user would never get a result.

**`mx` is never routed through a reverse proxy.** The client IP from the TCP connection is the
most valuable input of the whole analysis and a proxy would replace it with its own address.

### Entrypoint

`mx/entrypoint.sh` renders the template and then fixes two container specific problems.

**`envsubst` gets an explicit variable list.** Called without arguments it substitutes every `$`
reference in the file, including Postfix's own parameters, and silently produces a broken config.

**`postconf -F '*/*/chroot=n'`.** Debian runs `smtpd` and `lmtp` chrooted into
`/var/spool/postfix`, where there is no `/etc/resolv.conf` and no `/etc/hosts`, so container names
cannot be resolved and both the recipient map lookup and LMTP delivery fail with a 451. The
container itself is the isolation boundary, so the chroot only gets in the way.

**`postfix check` output is printed explicitly.** Because `maillog_file = /dev/stdout` routes
Postfix's own messages through its logging service, a failing check would otherwise exit with
status 1 and no visible reason.

**The template lives in `/etc/mailtester/`,** not in `/etc/postfix/`, because Postfix scans its
configuration directory and warns about any file it finds there with group or other write
permission.

**A self signed certificate is generated when none is mounted.** Most senders use opportunistic
TLS, so this is enough to encrypt the connection and to report a TLS version. Mounting a real
certificate is still preferable:

```yml
mx:
  volumes:
    - /etc/letsencrypt/live/mx.example.com/fullchain.pem:/etc/postfix/tls/mx.crt:ro
    - /etc/letsencrypt/live/mx.example.com/privkey.pem:/etc/postfix/tls/mx.key:ro
```

---

## DNS Resolution

`dns/unbound.conf` defines a recursive resolver with **no forward zone**, and that is the whole
point. Spamhaus and most other DNSBL providers refuse queries coming from public resolvers such as
`8.8.8.8` and answer with an error code in `127.255.255.0/24`. Code that treats any successful
answer as a listing then reports clean IP addresses as blacklisted, which destroys the credibility
of the whole report.

Two things follow from this:

- Every DNS lookup in `src/processor/service.py` goes through `get_resolver()`, so SPF, DKIM,
  DMARC, PTR, MX, A and DNSBL queries all use the same resolver.
- A `127.255.255.x` answer is reported as `blocked`, never as `listed`. A check that could not run
  says so instead of guessing.

The resolver can be verified with the addresses the blocklists reserve for testing: `127.0.0.2` must
come back listed on most of them and `127.0.0.1` must come back listed on none. If `zen.spamhaus.org`
reports `blocked` for `127.0.0.2`, queries are leaving through a public resolver and every blacklist
result in the report is worthless.

Access is restricted to private ranges, so the resolver is not reachable from outside the Docker
network.

---

## Requirements

- A server with **inbound TCP port 25 open**. Nothing else needs to be reachable from outside.
- A domain whose MX record points to that server.
- Docker and Docker Compose.

Port 587 stays closed. This service only receives mail, so there is no submission port, no SASL and
no IMAP, which means there are no credentials to brute force.

---

## DNS Records

For `example.com` on server `1.2.3.4`:

```
example.com.          MX   10 mx.example.com.
mx.example.com.       A       1.2.3.4
www.example.com.      A       1.2.3.4
example.com.          TXT     "v=spf1 -all"
_dmarc.example.com.   TXT     "v=DMARC1; p=reject;"
```

The SPF and DMARC records say "this domain never sends mail", which stops anyone from spoofing it.
If you later send mail from the domain, add the sending host to the SPF record.

Do not add a wildcard A or CNAME record. Setting the server's PTR record to `mx.example.com` is
recommended but not required, since the service only receives mail.

---

## Quickstart

```bash
cp .env.example .env
docker compose build
docker compose up -d
docker compose logs -f mx ingest
```

`mx` prints the hostname, domain and LMTP target on startup, `ingest` prints its two listening
ports. `GET /health` reports whether a web interface is mounted. Swagger is at `/docs`.

### Environment variables

| Variable | Meaning |
|---|---|
| `MAIL_DOMAIN` | Domain of the test addresses. Its MX record must point at this server. |
| `MX_HOSTNAME` | Name Postfix announces and writes into `Received`. Defaults to `mx.$MAIL_DOMAIN`. |
| `MONGODB_URI` | Full connection string. Keep the password alphanumeric so it needs no URL encoding. |
| `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD`, `MONGO_DB_NAME` | Used when the database is first created. |
| `REDIS_URL` | Live test addresses and SSE pub/sub. Kept on a different database number than Celery. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Task queue. |
| `INGEST_HOST`, `INGEST_LMTP_PORT`, `INGEST_MAP_PORT` | Where Postfix reaches the ingest service. |
| `MESSAGE_SIZE_LIMIT` | Maximum accepted message size in bytes, enforced by both Postfix and ingest. |
| `TEST_ADDRESS_TTL_MINUTES` | How long a generated address stays live. |
| `ANON_DAILY_LIMIT` | Analyses per day for a visitor without an account, counted per IP address. |
| `USER_DAILY_LIMIT` | Analyses per day for a registered user. Also written onto the user document at registration. |
| `GENERATE_RATE_LIMIT`, `GENERATE_RATE_WINDOW` | Requests to `/generate` allowed per window, per user or per IP. |
| `AUTH_RATE_LIMIT`, `AUTH_RATE_WINDOW` | Requests to `/register` and `/login` allowed per window, per IP. |
| `PASSWORD_MIN_LENGTH` | Minimum password length at registration. |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | STARTTLS certificate paths. A self signed pair is generated if the files are missing. |
| `DNS_RESOLVER` | Hostname of the unbound container. Leaving it empty falls back to the system resolver and makes blacklist results unreliable. |
| `DNS_TIMEOUT`, `DNS_LIFETIME` | General DNS query budget. |
| `DNSBL_TIMEOUT`, `DNSBL_LIFETIME`, `DNSBL_MAX_LISTS`, `DNSBL_CONCURRENCY` | Blacklist query budget and fan out. |
| `SPAMD_HOST`, `SPAMD_PORT`, `SPAMD_TIMEOUT` | SpamAssassin daemon. |
| `SSE_TIMEOUT` | Maximum lifetime of one SSE connection in seconds. |
| `SECRET_KEY`, `ALGORITHM`, `TOKEN_EXPIRE_MINUTES` | Token signing. |
| `WEB_ROOT` | Directory served at the root path. Defaults to `public`. |

---

## Web Interface

This repository ships the API and the SMTP ingest only, without a user interface. Mount a directory
containing an `index.html` at `/app/public` and the API serves it from the root path on the same
origin, so there is nothing to configure for CORS:

```yml
api:
  volumes:
    - /opt/my-frontend:/app/public:ro
```

Without that mount the API runs on its own and `/` returns a small JSON pointing at `/docs`.

Paths whose components begin with a dot are answered with 404. A mounted directory is usually a git
working tree, and `StaticFiles` would otherwise serve `/.git/config` and let anybody reconstruct
the repository from `/.git/objects`.

Files ending in `.md`, `.yml`, `.yaml`, `.toml`, `.ini`, `.log`, `.bak` or `.sql` are answered with
404 for the same reason. A repository mounted as a web root almost always contains a README and
sometimes a compose file, and neither is meant to be readable by visitors. Note that `robots.txt`,
`sitemap.xml`, `LICENSE.txt` and every real asset are unaffected.

---

## Deployment Specific Configuration

`docker-compose.yml` is meant to run anywhere without changes. Anything belonging to one particular
server goes into `docker-compose.override.yml`, which Docker Compose merges automatically and which
is not committed. An example for a host that already runs Traefik ships as
`docker-compose.traefik.yml`:

```bash
cp docker-compose.traefik.yml docker-compose.override.yml
docker compose up -d
```

Adjust the `Host()` rule, the entrypoint name, the certificate resolver name and the web root path
to match your setup.

Joining Traefik's network does not change Traefik itself. It discovers containers through the
Docker API and reads their labels, so adding a labelled container is purely additive: no restart
and no configuration change on the existing stack. Keep the router and middleware names unique so
they cannot collide with another project.

The example does not override the `ports` entry. The base file already binds the API to
`127.0.0.1`, Traefik reaches the container over its own network, and the local binding stays useful
for debugging. Avoiding the override also avoids Compose's `!override` tag, which older versions do
not understand.

The example sets a content security policy of `default-src 'self'`. The analysed message is
attacker controlled data, and if any of it ever escapes into the page, the policy stops it from
executing. The interface uses no inline scripts or styles, so `'self'` is sufficient.

---

## Limits

There are two independent mechanisms, both configured from the environment.

**Daily quota** is the number of analyses allowed per day. Anonymous visitors are counted by IP
address, registered users by a counter on their own document, which is why registering is worth it:
`ANON_DAILY_LIMIT` defaults to 5 and `USER_DAILY_LIMIT` to 25.

The quota is charged when an analysis actually starts, not when an address is generated, so an
address that never receives a mail costs nothing. `/generate` still refuses immediately with `429`
once the quota is used up, so the user finds out before sending a mail instead of after.

The charge happens exactly once per address. The worker claims an address with a conditional update
on `analysis_started_at`, so a task that is delivered twice cannot be billed twice.

**Request rate** protects the endpoints from abuse. Without it anybody could create unlimited test
addresses, because generating one is cheap while storing it is not. The counter is a Redis key per
scope and identity with a TTL, and the response carries `Retry-After`. If Redis is unreachable the
request is allowed rather than blocked, since losing rate limiting is better than losing service.

`GET /limits` returns the caller's own quota and is what the interface shows. If Redis or MongoDB
counters are unavailable the endpoint still answers, so the interface never blocks on it.

---

## Accounts

- `POST /register` – JSON body with `email` and `password`
- `POST /login` – form encoded `username` and `password`, returns a bearer token
- `POST /logout` – deletes the token server side, so it stops working immediately
- `GET /me` – email and remaining quota of the current token
- `POST /generate` – accepts an optional `Authorization: Bearer <token>` header

Email addresses are normalised to lower case on both registration and login, so the same account
can always be reached regardless of how it is typed.

A failed login answers `401 Invalid email or password` whether or not the address exists. Different
messages for the two cases would turn the login form into a way of checking which addresses are
registered.

---

## Result Statuses

`GET /result/{to_address}` may return:

- `waiting` – no mail has arrived yet
- `processing` – analysis running
- `analyzed` – analysis completed, the result is in the response
- `expired` – the address expired before any mail arrived
- `error` – analysis failed, `detail` explains why

`GET /result/{to_address}` returns the newest analysed message for the address, without the `after`
filter. It keeps working after the address itself has expired, because results live on
`mail_events` and `analyses`, which have no TTL.

There is deliberately no server push. The interface is driven by a button, so it polls `/check`
about once every two seconds while waiting and gives up after three minutes. Server sent events
would add a second source of truth for the same question and buy nothing a person would notice.

---

## Scoring System

- Every email starts with 10 points
- Points are reduced when problems are detected
- Each issue explains what is wrong, how much it costs and how to fix it

### Example Score Reductions

- Missing SPF record → -2.0
- Missing DKIM record → -1.0
- Missing DMARC record → -1.0
- Missing important headers → -0.5
- Reverse DNS mismatch → -0.4
- IP listed in blacklists → -0.5

### Final Score Meaning

- 9–10 → Excellent
- 7–8.9 → Good
- 5–6.9 → Average
- Below 5 → Poor

An SPF check only counts TXT records that start with `v=spf1`. A domain can have many TXT records
for unrelated verifications, and treating any of them as an SPF record reports a missing policy as
present.

---

## Database

The MongoDB client is created with `connect=False`. Celery's prefork pool forks after the module is
imported, and a client that has already opened its monitoring threads and sockets is not fork safe;
PyMongo warns about it and it can deadlock. Deferring the connection means each forked child opens
its own.

### MongoDB Indexes (Required)

```bash
mongosh

use <your_database_name>

db.test_emails.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })
db.test_emails.createIndex({ to_address: 1 }, { unique: true })
db.mail_events.createIndex({ to_address: 1, _id: -1 })
db.mail_events.createIndex({ created_ip: 1, analyzed_at: 1 })
db.users.createIndex({ email: 1 }, { unique: true })
db.tokens.createIndex({ token: 1 }, { unique: true })
```

The unique index on `users.email` is what actually prevents duplicate accounts. Registration checks
for an existing address first, but two simultaneous requests can both pass that check, and only the
index stops the second insert.

The two `mail_events` indexes matter: the first serves every `/check` call, which looks up the newest
message for an address, and the second serves the anonymous daily quota, which counts analysed
messages by IP address and would otherwise scan the collection.

The TTL index removes expired test addresses. Analyzed records get their `expires_at` field
removed so a finished result cannot be deleted by it. The third index serves the anonymous quota
count, which would otherwise scan the collection as data grows.

---

## Local Testing Without DNS

The mail path can be exercised on a laptop by pointing `mx` at a stub that speaks the same two
protocols, so no domain and no port 25 are needed:

```bash
docker build -f mx/Dockerfile -t mx-test .
docker run -d --rm --name mx-test \
  -e MAIL_DOMAIN=example.com -e MX_HOSTNAME=mx.example.com \
  --add-host=ingest:host-gateway -p 12525:25 mx-test
```

A stub answering `200 OK` on port 2500 and accepting LMTP on port 2400 is enough to verify
recipient rejection, relay denial, STARTTLS and delivery. `docker logs mx-test` shows the outcome
of each delivery attempt; a working setup ends with `status=sent (250 Message accepted)`.

---

## Usage Flow

1. `POST /generate` → temporary test address
2. Send an email to that address
3. `GET /check/{to_address}` until the status is `analyzed`
4. Send another mail to the same address and call
   `GET /check/{to_address}?after=<event_id>` to get the newer result
