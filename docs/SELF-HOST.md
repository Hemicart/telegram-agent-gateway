# Self-hosting

Every target needs: **an always-on instance, a persistent disk at `/data`, an `https://` address,
and exactly one running copy.** After deploying, open the `https://` address straight away and
press **Start**: the first browser to do so owns the server. From another browser later, sign in
with the same `api_id` + `api_hash` you entered in setup.

Want a password on that first step anyway? Set `SETUP_PASSWORD` on your host before deploying.

## Render (recommended)

About 10 minutes, all in the browser. Cost: Starter plan ~$7/month + ~$0.25/month for the 1 GB disk.

1. Log in at [dashboard.render.com](https://dashboard.render.com) with GitHub. When GitHub asks,
   choose **Only select repositories** and pick your copy only.
2. Click **+ New → Blueprint**. Not "New Web Service": that form skips the disk and you would
   have to fill everything in by hand.
3. Pick your copy. If the **Blueprint Path** box is empty, click it and choose `render.yaml`.
4. Review: one service `telegram-agent-gateway`, Starter plan, disk `tgdata` at `/data`.
   Click **Deploy Blueprint**. Render asks for a card for the Starter plan.
5. Wait for **Live** (3–5 minutes for the first build). Open the service: the
   `https://….onrender.com` address is at the top. **Open it right away and press Start.**
   The first browser to press Start becomes the owner.

`render.yaml` already sets: Docker, the disk at `/data`, health check `/health`, exactly one
instance, and auto-deploy when **your copy** changes. Don't change the instance count.

To update later: ask your agent to update your copy from the original (commands in the README's
agent section). Render redeploys by itself in a few minutes; your login and settings stay on the disk.

## Railway

New sign-ups are paused after Railway's October 2026 security incident. If you already have an
account:

1. New Project → Deploy from GitHub repo → your copy. Grant the Railway GitHub App access to
   **only this repository**.
2. Service → Settings → Volumes → add a volume mounted at `/data`.
3. Settings → Networking → Generate Domain. Open the `https://…up.railway.app` address.
4. Open it right away and press **Start**.

Keep replicas at 1.

## Fly.io

```bash
fly volumes create tgdata --size 1
fly launch --copy-config --ha=false
```

Never `fly scale count 2`. Do not use the bluegreen strategy.

## DigitalOcean droplet

DigitalOcean's App Platform has no persistent disk, so use a droplet (about $6/month). It sets
itself up from a cloud-init file: Docker, the gateway, and Caddy for HTTPS. No domain needed: it
uses `<droplet-ip>.sslip.io`.

1. Copy [`deploy/digitalocean/cloud-init.yaml`](../deploy/digitalocean/cloud-init.yaml) and change
   `YOUR_GITHUB_USER` to your GitHub name (your public fork works as-is).
2. DigitalOcean → **Create → Droplets**: Ubuntu 24.04, Basic, the $6 size, any region.
   Authentication: your SSH key.
3. **Advanced Options → Add Initialization scripts** → paste the file. Create the droplet.
4. After about 3 minutes, open `https://<droplet-ip>.sslip.io` (the IP is on the droplet page)
   and press **Start**. It asks for a **setup password**: droplet addresses are public, so the
   droplet makes one. See it with `ssh root@<ip>` (shown on login) or the droplet's web **Console**.

If it doesn't come up: `ssh root@<ip>` then `cat /var/log/telegram-agent-gateway-setup.log`.
Update later: `ssh root@<ip> sh /opt/telegram-agent-gateway/deploy/digitalocean/update.sh`.
Own domain instead of sslip.io: point an A record at the droplet, put `DOMAIN=your.domain` in
`/opt/telegram-agent-gateway/deploy/digitalocean/.env`, and run `update.sh`.

## VPS / Docker

```bash
docker compose up -d
```

Compose publishes `127.0.0.1:8080` only. Put Caddy in front with a real domain:

```
gateway.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy gets the certificate and sends `X-Forwarded-Proto: https`. Without HTTPS the setup page
refuses to run (except on localhost).
