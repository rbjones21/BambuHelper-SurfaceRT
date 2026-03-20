# GitHub Repo Setup Guide

This guide connects your BambuHelper install to GitHub so the Surface RT
can pull updates with a single command.

---

## Step 1 — Create the GitHub repo

1. Go to https://github.com/new
2. Name it: `BambuHelper-SurfaceRT`
3. Set it to **Public** (required for the updater to fetch raw files without a token)
4. Click **Create repository**

---

## Step 2 — Push the initial code

On your PC, in the `bambuhelper-surface-v2` folder:

```bash
git init
git add .
git commit -m "Initial release v1.0.0"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/BambuHelper-SurfaceRT.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your actual GitHub username.

---

## Step 3 — Update the updater script with your repo URL

Edit `bambu-update` on the Surface RT:

```bash
nano /usr/local/bin/bambu-update
```

Change these two lines at the top to match your repo:

```bash
REPO="https://github.com/YOUR_USERNAME/BambuHelper-SurfaceRT"
RAW="https://raw.githubusercontent.com/YOUR_USERNAME/BambuHelper-SurfaceRT/main"
```

---

## Step 4 — Test it

```bash
# Check current vs remote version
sudo bambu-update --check

# Apply update
sudo bambu-update
```

---

## Workflow going forward

When you ask for code changes and I provide updated files:

### On your PC
```bash
# Copy new files into your repo folder, then:
git add .
git commit -m "Description of what changed"
git push
```

### On the Surface RT
```bash
sudo bambu-update
```

That's it. The updater will:
- Show you the current vs new version number
- Ask for confirmation before applying
- Back up the current install automatically
- Restart the services
- Leave your config.json completely untouched

---

## Rollback if something goes wrong

```bash
sudo bambu-rollback
```

Restores the previous version automatically.

---

## Version numbers

The `version.txt` file in the repo root controls the version number.
When I provide an update, I'll tell you what to change it to (e.g. 1.0.1, 1.1.0).
The updater compares local vs remote version.txt to decide if an update is available.

---

## What the updater touches vs what it leaves alone

| File | Updated? |
|---|---|
| `/opt/bambuhelper/bambu_server.py` | ✅ Yes |
| `/opt/bambuhelper/templates/dashboard.html` | ✅ Yes |
| `/opt/bambuhelper/templates/settings.html` | ✅ Yes |
| `/opt/bambuhelper/version.txt` | ✅ Yes |
| `/etc/bambuhelper/config.json` | ❌ Never — your printer settings are safe |
