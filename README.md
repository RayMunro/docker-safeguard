<p align="center">
  <img src="app/static/icon.png" width="160" alt="Docker Safeguard icon" />
</p>

# Docker Safeguard

A self-hosted backup/restore tool for Docker apps on Unraid. Pick a container,
click **Backup now**, and it captures both the appdata *and* everything
needed to recreate the container exactly as it was — image, ports, env vars,
mounts, network settings, and Unraid's own template (icon, category, WebUI
link) — into one archive. Restore it later (on the same box, or a clean
install) and the app comes back up instantly runnable with the same data and
options, not just files dumped into a folder.

Copyright © 2026 Ray Munro. Licensed under the [GNU GPLv3](LICENSE).

## Running on Unraid

### Option A: install from the template (recommended)

1. In Unraid's **Docker** tab, click **Add Container**, switch to
   **advanced view**, and set **Template** to:
   ```
   https://raw.githubusercontent.com/RayMunro/docker-safeguard/main/templates/docker-safeguard.xml
   ```
   Or copy the template into place directly over SSH so it's available
   without visiting the page first:
   ```bash
   curl -o /boot/config/plugins/dockerMan/templates-user/my-docker-safeguard.xml \
     https://raw.githubusercontent.com/RayMunro/docker-safeguard/main/templates/docker-safeguard.xml
   ```
2. Review the mounts (see **What it needs access to**, below) and the WebUI
   port, then **Apply**. The image is pulled from GitHub Container Registry —
   no local build required.
3. Open the WebUI, set a password on first run, and you're in.

### Option B: docker run by hand

```bash
docker run -d \
  --name docker-safeguard \
  --restart unless-stopped \
  -p 8091:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /mnt/user:/mnt/user \
  -v /mnt/disks:/mnt/disks \
  -v /mnt/remotes:/mnt/remotes \
  -v /boot/config/plugins/dockerMan/templates-user:/unraid-templates \
  -v /mnt/user/appdata/docker-safeguard:/config \
  --label net.unraid.docker.managed=dockerman \
  --label 'net.unraid.docker.webui=http://[IP]:[PORT:8000]/' \
  --label 'net.unraid.docker.icon=https://raw.githubusercontent.com/RayMunro/docker-safeguard/main/app/static/icon.png' \
  ghcr.io/raymunro/docker-safeguard:latest
```

The `--label` flags make Unraid's Docker tab show a WebUI button and icon
even though the container wasn't created from the template.

### Option C: build from source

```bash
git clone https://github.com/RayMunro/docker-safeguard.git
cd docker-safeguard
docker build -t docker-safeguard:local .
```

Then run it the same way as Option B, swapping the image for
`docker-safeguard:local`.

## What it needs access to

Docker Safeguard needs fairly broad access to do its job, so it's worth
understanding what each mount is for:

| Mount | Why |
|---|---|
| `/var/run/docker.sock` | Inspect, create, stop, and start containers. This is root-equivalent access to the host — the same level of trust you'd give Unraid's own Docker tab. |
| `/mnt/user` | Read appdata to back it up, and write backup archives to any share. |
| `/mnt/disks`, `/mnt/remotes` | Back up to (or restore from) a USB/NVMe/HDD mounted via Unassigned Devices, or a network remote. |
| `/boot/config/plugins/dockerMan/templates-user` | Read/write Unraid's own per-container template XML, for high-fidelity backup and restore (icon, category, WebUI link). |
| `/mnt/user/appdata/docker-safeguard` (`/config`) | The app's own login, settings, and job history. Nothing else lives here. |

Because of the Docker socket mount, anyone who can reach the web UI has
effective root on the host. It sets its own password on first run for
exactly this reason — don't expose it outside your LAN/VPN without also
putting something else (a reverse proxy with auth, Tailscale, etc.) in front
of it.

A restore always writes into one of the mounts above; if a backup's original
data path somehow falls outside all of them *and actually has data in it*,
Docker Safeguard refuses to restore rather than silently losing it — see the
in-app warning if that ever happens. An unreachable path with nothing
archived in it (e.g. a plugin's own mount point, not real appdata) is just
skipped instead of blocking the whole restore.

## What gets backed up

- **Appdata**: any bind-mounted path under `/mnt/user/appdata` or
  `/mnt/cache/appdata` is included by default. Anything else the container
  mounts (a media library, downloads, etc.) is detected and shown, but
  excluded by default — opt in per-path if you want it archived too.
- **Container config**: image, tag, environment variables, port mappings,
  restart policy, network mode (including a static IP on a macvlan network
  like Unraid's `br0`), capabilities, devices, and labels — captured via
  `docker inspect`, and it's this data (not the template) that actually
  recreates the container on restore.
- **The Unraid template**, if one exists for the container — restoring it
  gets you back the icon, category, and WebUI link in Unraid's Docker tab,
  not just a working container.
- **Named Docker volumes are not backed up** — almost nothing on Unraid uses
  these (bind mounts are the norm), but if a container does, it's flagged in
  the restore preview rather than silently skipped.
- The image itself isn't bundled; restore re-pulls `image:tag` from its
  registry. If a container uses a moving tag like `latest`, a restore months
  later may pull a newer image than the one originally running.

Archives are `<container>__<timestamp>.safeguard.tar.zst` — a single
zstd-compressed tar containing a `manifest.json` (everything above) plus the
included data, streamed rather than staged, so large appdata doesn't need
double the disk space to back up.

## Using it

- **Dashboard** — one card per container, with its real icon/status pulled
  from Docker, and a *Backup now* button.
- **Backup** — pick a destination (browse any share, or a mounted external
  drive), review which paths are included, and go. Advanced options let you
  skip stopping the container first, exclude glob patterns within a path, or
  include a normally-excluded mount. The destination picker pre-selects
  wherever that app was last backed up to, or your last-used folder if it's
  the first time. Clicking *Start backup* confirms the resolved destination
  path in a dialog before anything happens, with a "don't show this again"
  option once you trust your setup. A running backup can be cancelled — the
  partial archive is removed and the container is restarted if it had been
  stopped.
- **Restore** — browse to (or upload) a `.safeguard.tar.zst` file, picking up
  right where the folder browser last left off. You get a plain-language
  preview — image, ports, data size, conflicts — before anything happens,
  and a live progress view while it runs. A running restore can also be
  cancelled: unlike backup, files already written are left in place rather
  than deleted (they're real data, possibly merged into what was already
  there), but no container is created on top of a partial restore — just
  re-run it to finish the job.
- **Schedule** — recurring backups with daily/weekly/monthly presets or a raw
  cron expression, and a "keep last N" retention policy.
- **Logs** — history of every backup/restore, including failures.
- **Settings** — change your password, set a webhook/Apprise URL for
  success/failure notifications, and verify an existing archive's integrity
  without restoring it.

## Troubleshooting the icon

If the icon shows as a broken image or question mark in Unraid's Docker tab,
that's Unraid's own icon cache, not a broken URL — it downloads a container's
icon once and won't retry just because you reload the page. Force a refresh:

```bash
/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/dockerupdate nonotify
```

## Prebuilt image

Every push to `main` builds and publishes the image to GitHub Container
Registry via [a GitHub Actions workflow](.github/workflows/docker-publish.yml):

```
ghcr.io/raymunro/docker-safeguard:latest
```

This is what `templates/docker-safeguard.xml` uses — you don't need to build
anything yourself to run it this way.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=./data TEMPLATES_USER_DIR=./data/templates \
  SHARES_ROOT=./data/shares DISKS_ROOT=./data/disks REMOTES_ROOT=./data/remotes \
  .venv/bin/uvicorn app.main:app --reload
```

Then open `http://localhost:8000`. Without a real Docker socket mounted,
the dashboard will show a "can't reach Docker" banner instead of crashing —
useful for UI work, but you'll want a real Unraid box (or any Linux Docker
host) to test backup/restore end to end.
