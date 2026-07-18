# Deployment: Azure for Students

Deployed on an Azure for Students subscription (₹8,000 / $100 credit,
12-month trial). Superseded the original Oracle Cloud plan: Oracle's Always
Free Ampere A1 tier is a better long-term free option, but Azure credit was
already available and sufficient for this workload. See docs/decisions.md
for the reasoning.

## 1. Provision the VM

Resource group `internblog-rg-sea` (southeastasia). Networking was
provisioned first (VNet, NSG with 22/80/443 open, a static public IP, one
NIC), then the VM attached to that NIC — this keeps the public IP and DNS
record stable across any future VM rebuild.

```bash
az group create -n internblog-rg-sea -l southeastasia
az network vnet create -g internblog-rg-sea -n internblog-vnet --subnet-name default
az network nsg create -g internblog-rg-sea -n internblog-nsg
az network nsg rule create -g internblog-rg-sea --nsg-name internblog-nsg -n allow-ssh  --priority 100 --destination-port-ranges 22
az network nsg rule create -g internblog-rg-sea --nsg-name internblog-nsg -n allow-http --priority 110 --destination-port-ranges 80
az network nsg rule create -g internblog-rg-sea --nsg-name internblog-nsg -n allow-https --priority 120 --destination-port-ranges 443
az network public-ip create -g internblog-rg-sea -n internblog-pip --sku Standard --allocation-method Static
az network nic create -g internblog-rg-sea -n internblog-nic --vnet-name internblog-vnet --subnet default \
  --network-security-group internblog-nsg --public-ip-address internblog-pip

az vm create -g internblog-rg-sea -n internblog-vm \
  --nics internblog-nic \
  --image "Canonical:ubuntu-26_04-lts:server-arm64:latest" \
  --size Standard_B2pls_v2 \
  --admin-username ubuntu \
  --ssh-key-values ~/.ssh/id_rsa.pub \
  --os-disk-size-gb 30
```

**Sizing note — this matters:** the cheapest/free-tier-eligible x64 burstable
size in this subscription (`Standard_B2ats_v2`) has only **1 GB RAM**. That
is not enough for Postgres + FastAPI + Caddy + a headless Chromium (used for
session refresh) running together — it OOM'd and locked up the box hard
enough that even Azure's out-of-band run-command agent stopped responding,
requiring a VM restart to recover. Larger x64 burstable/general-purpose sizes
(`B2s`, `B2als_v2`, `D2s_v3`, etc.) were either out of capacity or
`NotAvailableForSubscription` in this region for this student subscription.
The Arm64 burstable family (`B2pls_v2`, 2 vCPU / 4 GB) *was* available and is
what's actually running. Arm64 requires an Arm64 OS image
(`Canonical:ubuntu-26_04-lts:server-arm64:latest` — Ubuntu 24.04 only ships
Arm64 images under the `-daily` offer, so 26.04 LTS was used instead) and
Azure will not resize an x64 VM to an Arm64 size in place; recreating the VM
is required, but the NIC/public IP/NSG can be reused so DNS is unaffected.
All Docker images used here (`postgres:16-alpine`, `caddy:2-alpine`, and our
own `python:3.12-slim`-based Dockerfile) are multi-arch and need no changes
for Arm64.

## 2. Point a domain at it

DuckDNS (free): create `<name>.duckdns.org`, point it at the VM's static
public IP. Caddy needs a real hostname for Let's Encrypt — a bare IP won't
work.

## 3. Install Docker

```bash
ssh ubuntu@<instance-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
sudo apt install -y docker-compose-plugin
```

## 4. Copy the project and one-time session state

No GitHub remote is configured for this repo, so the project is pushed
directly via `rsync` rather than `git clone`:

```bash
rsync -az --exclude='.git' --exclude='__pycache__' --exclude='.venv' \
  --exclude='data' --exclude='logs' --exclude='.pytest_cache' \
  --exclude='*.sqlite' --exclude='*.db' \
  ./ ubuntu@<instance-ip>:~/internblog/
```

The blog login itself must stay manual (SSO constraint), so run
`scripts/reauth.py` **once, locally**, then copy the resulting session state
to the server (it will keep itself alive after that via the silent-refresh
path already built into the pipeline — see the known-issue note below):

```bash
rsync -az storage_state.json browser_profile ubuntu@<instance-ip>:~/internblog/
```

## 5. Configure `.env`

Keep secrets in the local `.env` (not just typed directly on the server) so
a future `rsync` doesn't silently wipe server-only values:

```bash
cp .env.example .env
# fill in: POSTGRES_PASSWORD, GROQ_API_KEY, TELEGRAM_BOT_TOKEN,
# CALENDAR_FEED_TOKEN (any long random string)
echo "CADDY_DOMAIN=your-hostname.duckdns.org" >> .env
```

## 6. Start it

```bash
docker compose up -d --build
docker compose logs -f caddy   # confirm cert issuance
curl https://your-hostname.duckdns.org/health
```

## 7. Wire up notifications

**Telegram**: message [@BotFather](https://t.me/BotFather) to create a bot
and get `TELEGRAM_BOT_TOKEN` (set once, globally, in `.env`). Each signed-in
user then sets their own chat id from `/settings`: send the bot any message,
visit `https://api.telegram.org/bot<token>/getUpdates` to read their `chat_id`
from the JSON response, and paste it into the form.

**Google Calendar**: Google Calendar → Settings → Add calendar → From URL →
`https://your-hostname.duckdns.org/calendar/<CALENDAR_FEED_TOKEN>.ics`.
Google polls subscribed external ICS feeds on its own schedule (commonly
every several hours, not instantly) — same limitation Codeforces' contest
calendar has. Telegram remains the immediate-notification channel; the
calendar feed is for deadline tracking over time, not real-time alerts.

## 8. Public login setup (multi-user)

1. In the Google Cloud project already used for Calendar API access, add
   an OAuth 2.0 **Web application** client (not "Desktop app" like the
   original single-user setup) with an authorized redirect URI of
   `https://your-hostname.duckdns.org/auth/callback`.
2. Move the OAuth consent screen from "Testing" to "Production" (or add
   test users) - only accounts explicitly allowed under "Testing" can sign
   in otherwise, and the whole point is to let anyone sign in.
3. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
   `OAUTH_REDIRECT_BASE_URL`, `OWNER_EMAIL`, and `SECRET_ENCRYPTION_KEY` in
   `.env`.
4. Run `python scripts/migrate_owner_to_users_table.py` once, using the
   original `GOOGLE_CALENDAR_REFRESH_TOKEN`/`GOOGLE_CALENDAR_ID`/
   `TELEGRAM_CHAT_ID` values from before this migration, to carry the
   owner's existing setup into the new `users` table.
5. Remove the old `GOOGLE_CALENDAR_CLIENT_ID`/`_SECRET`/
   `GOOGLE_CALENDAR_REFRESH_TOKEN`/`GOOGLE_CALENDAR_ID`/`TELEGRAM_CHAT_ID`
   values from `.env` - they're no longer read by the app.
6. `docker compose up -d --build` to pick up the new dependency
   (`authlib`) and code.

## Known issue: silent session refresh is broken under this deployment

`app/session_refresh.py` calls Playwright's **sync** API
(`sync_playwright()`), but it runs inside FastAPI's async event loop
(uvicorn), which raises `Playwright Sync API inside the asyncio loop` and
prevents refresh from happening at all in this deployment shape. Needs
`async_playwright` (or running the sync call in a thread executor) — not yet
fixed.
