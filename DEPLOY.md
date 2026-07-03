# Hosting the leaderboard online (free, on all your devices)

This publishes your leaderboard to **GitHub Pages** (free, HTTPS, global CDN) and
refreshes it every 6 hours with **GitHub Actions** (free scheduled runs that
scrape, route Digitransit, send your Gmail alerts, and rebuild the page).

The page is **AES-encrypted with a passphrase** — anyone visiting the URL sees a
lock screen; only your passphrase decrypts it in the browser. So even though the
site is on a public URL, the content is private.

> **Why a *public* repo?** Free GitHub Pages only works on public repositories.
> That's fine here: the *code* is public (nothing secret in it), but your
> **listings stay private** (the page is encrypted), your **credentials stay
> private** (stored as encrypted Actions Secrets, never in the repo), and the
> database/site are never committed. If you'd rather keep everything private,
> use the Oracle free-VM route instead — ask and I'll write that guide.

## One-time setup (~10 minutes)

### 1. Put the code on GitHub
Install [Git](https://git-scm.com/) and create a free [GitHub](https://github.com)
account if you don't have them. Then, in this folder:

```bash
cd D:/GitRepos/house-finder
git init
git add .
git commit -m "House finder"
```
Create a new **public** repo on GitHub named e.g. `house-finder` (don't add a
README/.gitignore — the repo should be empty), then:

```bash
git branch -M main
git remote add origin https://github.com/<your-username>/house-finder.git
git push -u origin main
```
`.env`, the database, and the `site/` folder are git-ignored, so **no secrets or
data are uploaded** — only code.

### 2. Add your secrets
On GitHub: **repo → Settings → Secrets and variables → Actions → New repository
secret**. Add these five (values only — they're encrypted and never visible):

| Secret name | Value |
|---|---|
| `GMAIL_USER` | `sromic@gmail.com` |
| `GMAIL_APP_PASSWORD` | your Gmail App Password (ideally a **fresh** one — see note) |
| `NOTIFY_TO` | `sromic1@gmail.com` (where alerts go) |
| `DIGITRANSIT_KEY` | your Digitransit key |
| `SITE_PASSWORD` | **a passphrase you choose** — this unlocks the site |

### 3. Turn on Pages
**Settings → Pages → Build and deployment → Source: GitHub Actions.** (No branch
to pick — the workflow deploys it.)

### 4. Run it
**Actions → "Update leaderboard" → Run workflow** (or just wait for the next
6-hour tick). First run takes ~2 minutes.

### 5. Open it anywhere
Your URL is **`https://<your-username>.github.io/house-finder/`** (shown in the
deploy step's summary). Open it on your phone/iPad/any browser, enter your
`SITE_PASSWORD`, and the leaderboard appears. It remembers the passphrase for the
browser session.

## Good to know
- **Schedule**: every 6 h (UTC). Change the `cron` line in
  `.github/workflows/update.yml` to refresh more/less often.
- **Emails** still fire from the cloud run whenever a listing enters your top 5.
- **Rotate the Gmail App Password** if the current one was ever shared — create a
  new one and update the `GMAIL_APP_PASSWORD` secret; nothing else changes.
- **Change criteria** anytime by editing `config.yaml` and pushing — the next run
  picks it up.
- **Inactivity**: GitHub pauses scheduled workflows if a repo has *no activity*
  for 60 days. Any push (or clicking "Run workflow") re-arms it.
- **Updating the code** later: `git add . && git commit -m "…" && git push`.
