# Futu OpenD First-Time Login & Terminal Access

> **Scope:** Operational guide for first-time Futu OpenD setup in the SAM Trader V3 Docker stack.  
> **Prerequisite:** Docker Desktop (macOS) or Docker Engine (Linux) is installed and running.  
> **Relevant files:** `docker/docker-compose.yml`, `docker/Dockerfile.futu-opend`, `docker/futu-opend/start.py`

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Generate Your MD5 Password](#2-generate-your-md5-password)
3. [Start the Futu OpenD Container](#3-start-the-futu-opend-container)
4. [Complete the Regulatory Questionnaire](#4-complete-the-regulatory-questionnaire)
5. [Access the OpenD Telnet Console](#5-access-the-opend-telnet-console)
6. [Verify OpenD Is Healthy Before Starting sam-trader](#6-verify-opend-is-healthy-before-starting-sam-trader)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Quick Start

```bash
# 1. Set credentials in .env
FUTU_ENABLED=true
FUTU_ACCOUNT_ID=your_futubull_account
FUTU_ACCOUNT_PWD_MD5=your_md5_hashed_password

# 2. Start only the Futu OpenD container
docker compose --profile futu up -d sam-futu-opend

# 3. Check logs for the questionnaire URL
docker logs -f sam-futu-opend

# 4. After completing the questionnaire, verify health
docker compose ps sam-futu-opend
```

---

## 2. Generate Your MD5 Password

Futu OpenD requires the account password as a **32-character MD5 hash** (hexadecimal, lowercase).  
Never store your plaintext password in environment variables if you can avoid it.

### On macOS / Linux

```bash
echo -n 'your_plaintext_password' | md5sum
# Example output: 5f4dcc3b5aa765d61d8327deb882cf99  -
```

> **Note:** The `-n` flag is critical — it suppresses the trailing newline. Without it the hash will be wrong.

### If `md5sum` is unavailable (macOS without coreutils)

```bash
# Using openssl
printf '%s' 'your_plaintext_password' | openssl md5
# Using Python (always available if sam-trader venv is activated)
python3 -c "import hashlib; print(hashlib.md5('your_plaintext_password'.encode()).hexdigest())"
```

### Set the hash in `.env`

```bash
# .env
FUTU_ACCOUNT_PWD_MD5=5f4dcc3b5aa765d61d8327deb882cf99
```

> The container startup script (`docker/futu-opend/start.py`) also accepts the legacy `FUTU_ACCOUNT_PWD` and hashes it automatically, but emitting a deprecation warning. Prefer `FUTU_ACCOUNT_PWD_MD5`.

---

## 3. Start the Futu OpenD Container

> **Note:** The `sam-futu-opend` image is intentionally lightweight (~46 MB compressed). On first start it downloads the Futu OpenD binary (~405 MB) to the persistent volume. This one-time download takes 1–3 minutes depending on your connection. Subsequent restarts use the cached binary and start much faster.

### Standalone (recommended for first-time setup)

```bash
docker compose --profile futu up -d sam-futu-opend
```

### With the full stack

```bash
# After you have verified OpenD is healthy
docker compose --profile futu up -d
```

### Default ports

| Service | Host Port | Container Port | Description |
|---------|-----------|----------------|-------------|
| Futu API | `11111` | `11111` | Protobuf API for market data & trading |
| Telnet   | `22222` | `22222` | Admin / debug console |

These can be overridden via `.env`:

```bash
FUTU_OPEND_PORT=11111
FUTU_OPEND_TELNET_PORT=22222
```

### Apple Silicon (M1/M2/M3) note

The Futu OpenD binary is **x86_64 only**. `docker-compose.yml` already sets:

```yaml
platform: linux/amd64
```

Docker Desktop on macOS will run it via Rosetta 2 emulation automatically.

---

## 4. Complete the Regulatory Questionnaire

On first login (or after a long period of inactivity), Futu OpenD may require you to complete a **regulatory questionnaire** (e.g., suitability assessment, risk disclosure).

### Extract the questionnaire URL from logs

```bash
docker logs sam-futu-opend | grep -i "questionnaire\|问卷\|survey\|url"
```

Typical log output looks like:

```
[Login] Account login required questionnaire completion.
[Login] Please open the following URL in your browser:
https://www.futunn.com/questionnaire/...?token=...
```

### Live log tail (if the container just started)

```bash
docker logs -f sam-futu-opend
```

Copy the URL into your desktop browser, complete the questionnaire, then return to the terminal.

### After completion

The container will **not** automatically re-attempt login. Either:

1. **Restart the container:**
   ```bash
   docker restart sam-futu-opend
   ```

2. **Or trigger a reconnect via telnet** (see §5).

---

## 5. Access the OpenD Telnet Console

Futu OpenD exposes a telnet admin interface on port `22222` (configurable).

### Connect

```bash
docker exec -it sam-futu-opend telnet localhost 22222
```

> If `telnet` is not installed inside the container, install it first:
> ```bash
> docker exec -u root sam-futu-opend apt-get update && apt-get install -y telnet
> ```

### Common telnet commands

| Command | Description |
|---------|-------------|
| `help` | List available commands |
| `status` | Show connection / login status |
| `reconnect` | Force re-login to Futu servers |
| `quit` | Close telnet session |

### Trigger reconnect after questionnaire completion

```bash
docker exec -it sam-futu-opend sh -c 'echo "reconnect" | nc localhost 22222'
```

> `nc` (netcat) may need to be installed: `docker exec -u root sam-futu-opend apt-get install -y netcat-openbsd`

---

## 6. Verify OpenD Is Healthy Before Starting sam-trader

The `sam-trader` container has a `depends_on` condition that waits for `sam-futu-opend` to report healthy, **but only when starting the full stack**. If you start `sam-trader` independently, verify manually first.

### 6.1 Docker health status

```bash
docker compose ps sam-futu-opend
# or
docker inspect --format='{{.State.Health.Status}}' sam-futu-opend
```

Expected: `healthy`

### 6.2 Three-layer health check breakdown

The container runs `/bin/healthcheck.sh` every 30 seconds:

| Layer | Check | Command inside container |
|-------|-------|--------------------------|
| **L1** | Process running | `pgrep -x FutuOpenD` |
| **L2** | API port accepting | `true > /dev/tcp/localhost/11111` |
| **L3** | No login failures in recent logs | `grep -iE "login fail\|conn failed\|auth fail"` in `/home/futu/.com.futunn.FutuOpenD/log` |

### 6.3 Manual verification commands

```bash
# L1: Process check
docker exec sam-futu-opend pgrep -x FutuOpenD

# L2: API port check from host
telnet localhost 11111
# or
docker exec sam-futu-opend sh -c 'true > /dev/tcp/localhost/11111 && echo "Port open"'

# L3: Check recent logs for errors
docker exec sam-futu-opend sh -c 'ls -t /home/futu/.com.futunn.FutuOpenD/log/*.log | head -1 | xargs tail -n 20'
```

### 6.4 Verify from sam-trader's perspective

```bash
# Check if sam-trader can reach OpenD over the Docker network
docker exec sam-trader sh -c 'nc -z sam-futu-opend 11111 && echo "Reachable"'
```

> **Do not start `sam-trader` until OpenD reports `healthy`.** Starting prematurely will cause the Nautilus Futu adapter to fail its initial connection and may require a restart of `sam-trader`.

---

## 7. Troubleshooting

### 7.1 Login failed / authentication failure

**Symptoms:**
- Healthcheck L3 fails: `Login failure pattern detected in FutuOpenD logs`
- Logs show: `login fail`, `authentication fail`, `account login error`

**Steps:**

1. **Verify MD5 hash correctness** — re-generate with `echo -n 'password' | md5sum` and compare.
2. **Check `.env` is loaded** — `docker compose` reads `.env` automatically only when it is in the same directory as `docker-compose.yml`. Ensure yours is at the project root.
3. **Inspect raw logs:**
   ```bash
   docker logs sam-futu-opend
   ```
4. **Check for questionnaire requirement** — see §4.
5. **Account lockout** — too many failed attempts may trigger a temporary lock. Wait 15 minutes and retry.

### 7.2 Connection failed / port refused

**Symptoms:**
- `telnet localhost 11111` → `Connection refused`
- Healthcheck L2 fails: `API port 11111 not accepting connections`

**Steps:**

1. **Container is still starting** — Futu OpenD can take 30–60 seconds to initialize. Wait and re-check:
   ```bash
   docker compose ps sam-futu-opend
   ```

2. **Port mapping collision** — another service may be using `11111` or `22222` on the host:
   ```bash
   lsof -i :11111
   lsof -i :22222
   ```
   Change ports in `.env` if necessary:
   ```bash
   FUTU_OPEND_PORT=11112
   FUTU_OPEND_TELNET_PORT=22223
   ```
   Then update `docker-compose.yml` host ports or re-create the container:
   ```bash
   docker compose --profile futu up -d --force-recreate sam-futu-opend
   ```

3. **Wrong `FUTU_OPEND_IP`** — the container must bind to `0.0.0.0` inside the container to accept connections from the Docker network. The compose file already sets this:
   ```yaml
   FUTU_OPEND_IP: ${FUTU_OPEND_IP:-0.0.0.0}
   ```
   Do **not** set it to `127.0.0.1` unless you only want localhost access inside the container.

### 7.3 Mounts denied on macOS

**Symptoms:**
- `Error response from daemon: Mounts denied: ...`
- Docker Desktop file-sharing error

**Steps:**

1. **Docker Desktop → Settings → Resources → File sharing** — ensure the project root directory (e.g., `/Users/<you>/Trading/sam_trader`) is listed.
2. **No host bind mounts on Futu OpenD** — the Futu OpenD container only uses a named volume (`futu_opend_data`). If you see mount errors, they likely come from another container (e.g., `sam-trader` or `sam-services`).
3. **Reset Docker file sharing** (if stuck):
   ```bash
   # Docker Desktop → Troubleshoot → Reset to factory defaults
   # (last resort — back up volumes first)
   ```

### 7.4 Container exits immediately

**Symptoms:**
- `docker compose ps` shows `Exit 1`
- No logs emitted

**Steps:**

1. **Missing required env vars** — `FUTU_ACCOUNT_ID` and `FUTU_ACCOUNT_PWD_MD5` are mandatory. Check:
   ```bash
   docker logs sam-futu-opend
   # Expected: ERROR: FUTU_ACCOUNT_ID is required
   ```
2. **Architecture mismatch on Apple Silicon** — ensure `platform: linux/amd64` is set in `docker-compose.yml` (already default).
3. **Corrupt image / failed download** — re-build:
   ```bash
   docker compose --profile futu up -d --build sam-futu-opend
   ```

### 7.5 Slow performance on Apple Silicon

Futu OpenD is an x86_64 binary running under Rosetta 2. Expect:
- Slightly slower cold-start (~10–20 s additional).
- Normal runtime performance once initialized.

If startup exceeds 2 minutes, check Docker Desktop resource limits:
- **Settings → Resources → CPUs / Memory** — allocate at least 2 CPUs and 4 GB RAM.

---

## 8. Phase 4 Validation — TradingNode Integration & Order Testing

Once OpenD is healthy, the next validation gate is a full paper-trade order through the Nautilus TradingNode.

### 8.1 Start the full stack

```bash
docker compose --profile futu up -d
```

Wait for all containers to report `healthy`:

```bash
docker compose ps
```

### 8.2 Verify TradingNode connectivity

Check `sam-trader` logs for successful Futu client registration:

```bash
docker logs -f sam-trader
```

Expected log lines:

```
[INF] DataClient-FUTU: Connected to Futu OpenD at sam-futu-opend:11111
[INF] ExecClient-FUTU: Connected to Futu OpenD at sam-futu-opend:11111
[INF] TradingNode: RUNNING
```

> **Note:** In `SIMULATE` mode you may see `Account discovery returned no accounts`. This is non-fatal for paper trading.

### 8.3 One-shot paper order test

Run a manual order test from inside the `sam-trader` container:

```bash
docker exec -it sam-trader python3 -c "
from futu import SysConfig
from sam_trader.adapters.futu.common import get_cached_futu_trade_context

SysConfig.set_init_rsa_file('/.futu/futu.pem')
ctx = get_cached_futu_trade_context(
    host='sam-futu-opend',
    port=11111,
    trade_env='SIMULATE',
    trd_market='US'
)
ret, data = ctx.place_order(
    code='US.F',
    price=15.00,
    qty=1,
    trd_side='BUY',
    order_type='NORMAL',
    time_in_force='DAY',
    trd_env='SIMULATE',
)
print('RET:', ret)
print(data)
ctx.close()
"
```

**Key parameters:**
- `code='US.F'` — Ford Motor Co. (low-priced, liquid, good for 1-share tests)
- `price=15.00` — limit price (adjust to current market)
- `qty=1` — single share to minimize risk
- `trd_side='BUY'` — **must be string**, not integer `TrdSide.BUY`
- `order_type='NORMAL'` — **must be string**, not integer
- `time_in_force='DAY'` — **must be string**, not integer
- `trd_env='SIMULATE'` — paper trading

### 8.4 Cancel the test order

```bash
docker exec -it sam-trader python3 -c "
from futu import SysConfig
from sam_trader.adapters.futu.common import get_cached_futu_trade_context

SysConfig.set_init_rsa_file('/.futu/futu.pem')
ctx = get_cached_futu_trade_context(host='sam-futu-opend', port=11111, trade_env='SIMULATE', trd_market='US')
# Replace ORDER_ID with the ID printed by place_order
ret, data = ctx.modify_order(order_id=ORDER_ID, qty=0, trd_env='SIMULATE')
print('RET:', ret)
print(data)
ctx.close()
"
```

### 8.5 Expected validation checklist

| Check | Command / Indicator | Pass Criteria |
|-------|---------------------|---------------|
| OpenD healthy | `docker compose ps sam-futu-opend` | Status: `healthy` |
| TradingNode running | `docker logs sam-trader \| grep "RUNNING"` | Line present |
| Data client connected | `docker logs sam-trader \| grep "DataClient-FUTU: Connected"` | Line present |
| Exec client connected | `docker logs sam-trader \| grep "ExecClient-FUTU: Connected"` | Line present |
| Order submitted | `place_order` returns `ret == 0` | `RET: 0` |
| Order cancelled | `modify_order` with `qty=0` returns `ret == 0` | `RET: 0` |

---

## 9. Known Runtime Fixes & Migration Notes

The following issues were discovered during Phase 3 paper-trading validation and resolved. If you encounter them, ensure your environment includes the fixes.

### 9.1 RSA encryption required for cross-network trading

**Symptom:** `place_order` fails with an encryption or security error when OpenD binds to `0.0.0.0`.

**Root cause:** Futu requires RSA encryption on the trading interface when listening on all interfaces (`FUTU_OPEND_IP=0.0.0.0`).

**Fix:**
1. Generate a 1024-bit RSA key:
   ```bash
   ssh-keygen -t rsa -b 1024 -m PEM -f docker/futu-opend/futu.pem -N ""
   ```
2. Ensure it is mounted into **both** containers in `docker-compose.yml`:
   ```yaml
   volumes:
     - ${PWD}/docker/futu-opend/futu.pem:/.futu/futu.pem:ro
   ```
3. Configure OpenD XML:
   ```bash
   FUTU_OPEND_RSA_FILE_PATH=/.futu/futu.pem
   ```
4. Configure Python SDK before creating any context:
   ```python
   from futu import SysConfig
   SysConfig.set_init_rsa_file('/.futu/futu.pem')
   ```

> **Security:** `*.pem` is in `.gitignore`. Never commit private keys.

### 9.2 Futu SDK enum strings vs integers

**Symptom:** `place_order` raises `TypeError` or submits with wrong side/type.

**Root cause:** `futu-api` expects **string** constants (e.g., `'BUY'`), not integer enum values (e.g., `TrdSide.BUY` which is `0`).

**Correct usage:**
```python
ctx.place_order(
    code="US.F", price=15.00, qty=1,
    trd_side='BUY',          # NOT TrdSide.BUY (integer 0)
    order_type='NORMAL',     # NOT OrderType.NORMAL (integer)
    time_in_force='DAY',     # NOT TimeInForce.DAY (integer)
    trd_env='SIMULATE',      # NOT TrdEnv.SIMULATE (integer)
)
```

### 9.3 sam-trader container permission error

**Symptom:** `PermissionError: [Errno 13] Permission denied: '/opt/sam_trader/.com.futunn.FutuOpenD'`

**Root cause:** The `sam` non-root user cannot write to `/opt/sam_trader` because the directory is owned by `root`.

**Fix:** Ensure `docker/Dockerfile` contains:
```dockerfile
RUN chown sam:sam /opt/sam_trader
```
**before** the `USER sam` directive.

### 9.4 .env hostname staleness (v2 → v3 migration)

**Symptom:** `sam-trader` cannot connect to PostgreSQL, Redis, or IB Gateway; logs show connection refused to unknown hosts.

**Root cause:** `.env` or `.env.example` still contains container names from `csam_trader_v2`.

**Stale → Correct mappings:**

| Stale (v2) | Correct (v3) | Service |
|------------|--------------|---------|
| `csam-postgres` | `sam-postgres` | PostgreSQL |
| `redis` | `sam-redis` | Redis |
| `ib-gateway` | `sam-ib-gateway` | IB Gateway |

**Fix:** Audit `.env`, `.env.example`, and `src/sam_trader/config.py` defaults. Replace all occurrences with the `sam-` prefixed names.

---

## See Also

- `docs/reference/BUILD_PHASE_0.md` — Docker stack hardening reference
- `docs/reference/BUILD_PHASE_3.md` — Futu execution adapter reference
- `docs/reference/BUILD_PHASE_4.md` — Futu instrument provider & TradingNode integration
- `docker/docker-compose.yml` — Full service definitions
- `docker/Dockerfile.futu-opend` — Image build instructions
- `AGENTS.md` — SAM Trader conventions and commands
