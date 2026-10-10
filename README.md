<div align="center">

<img src="static/img/logo.svg" alt="" width="72" height="72">

# Faculty Funds

**Know what's left in every fund, and how long it will last.**<br>
A private web app for faculty who don't have direct access to their own balances, built around **Workday** expense reports and **OneUSG Connect** payroll exports.

[![Release](https://img.shields.io/github/v/release/SustainableUrbanSystemsLab/FacultyFunds?display_name=tag&sort=semver&logo=github&label=release&color=db2777)](https://github.com/SustainableUrbanSystemsLab/FacultyFunds/releases/latest)
[![Build](https://img.shields.io/github/actions/workflow/status/SustainableUrbanSystemsLab/FacultyFunds/release.yml?branch=main&logo=githubactions&logoColor=white&label=build)](https://github.com/SustainableUrbanSystemsLab/FacultyFunds/actions/workflows/release.yml)
![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/django-5.2-092E20?logo=django&logoColor=white)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)
[![Conventional Commits](https://img.shields.io/badge/conventional%20commits-1.0.0-FE5196?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org)
[![SemVer](https://img.shields.io/badge/semver-2.0.0-3F4551)](https://semver.org)

[✨ Features](#-features) · [🚀 Quick start](#-quick-start) · [🧭 Workflow](#-typical-workflow) · [🐳 Deploy](#-deploy) · [🔐 Security](#-security-and-privacy) · [🏷️ Releases](#️-releases-and-versions) · [🛠️ Development](#️-development)

</div>

> [!IMPORTANT]
> **Data policy:** payroll exports contain other people's salary data. Before you put real data on a cloud host, check with your department IT or data steward that the host is an approved place for it.

## ✨ Features

| | | |
|---|---|---|
| 🏦 | **Three kinds of funds** | Federal grants, foundation and gift funds, and state funds, each with only the fields it needs. |
| 📥 | **Imports** | Upload Workday expense reports and OneUSG payroll worksheets (`.xlsx`, `.xls`, `.csv`, or PeopleSoft "Excel" downloads that are really HTML). Mappings are remembered, duplicates are skipped, and any import can be undone. |
| 📊 | **Budget vs. actuals** | Spent, pending, planned payroll, and what's left, now and projected to the end date. |
| 👩‍🎓 | **Payroll projections** | Monthly salary × effort, or hourly rate × hours for student workers, plus fringe, graduate tuition remission, and summer salary. |
| 🔥 | **Burn rate and runway** | Alerts for overspending, funds running out early, money about to lapse, and stale pending items. |
| 🧾 | **F&A** | Calculated from each fund's rate on MTDC categories, or taken from imported F&A lines. |
| 🕒 | **Pending entries** | Count a booked trip or an ordered item before it shows up in Workday, then reconcile it with the real transaction. |
| 🥪 | **Food on grants** | A Meals & hospitality category flags food charged to a federal grant, since awards often don't allow it. |
| 🔒 | **Private by design** | Single owner, passkeys or password plus authenticator-app 2FA, and names replaced by a keyed hash the moment a file is read. |

### Fund kinds

| Kind | For | Money | Expires |
|---|---|---|---|
| 🏛️ **Federal grant** | NSF, DOE, and other federal agencies | Award amount, optional budget per category, F&A rate | On the award's end date |
| 🎁 **Foundation / gift** | Fellowships, gifts, endowment income | The award, then top-ups as they arrive | Never |
| 🗓️ **State funds** | Departmental money | A new allocation each fiscal year | **June 30** (unspent money lapses; the app shows only the current year and warns when you're on pace to leave money behind) |

## 🚀 Quick start

Requires [uv](https://docs.astral.sh/uv/). Copy `.env.example` to `.env`, set `DJANGO_DEBUG=1`, and fill in `DJANGO_SECRET_KEY` and `PII_HMAC_KEY` with long random strings.

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open http://127.0.0.1:8000, sign in, and scan the QR code with an authenticator app. **Save the backup codes** that appear next.

<details>
<summary><b>🎲 Try it with fake data</b></summary>

<br>

```bash
uv run python manage.py seed_demo
```

This adds one fund of each kind and an appointment, and writes sample Workday and OneUSG files to `samples/demo/` for you to upload on the Import page.

</details>

## 🧭 Typical workflow

1. 🏦 **Add your funds.** Go to Funds → Add fund. Choose the kind, then enter the worktag ID exactly as Workday shows it (e.g. `GR00012345`), a name, and the amount. For a grant, also set the end date and F&A rate, and optionally split the award by category. Under *More options*, add the award ID (`AWD…`) or OneUSG project code so payroll rows match too. Later, use **Add funding** on the fund page for a top-up or, for state money, each new fiscal year's allocation.
2. 📐 **Set rates.** Under Settings → Fringe and tuition rates by fiscal year, enter each year's fringe rate and monthly tuition remission for each person type. Fiscal years run July–June, so FY2027 is July 2026 to June 2027. A year left blank reuses the closest earlier year. The defaults are Georgia Tech's FY2025 values.
3. 📥 **Import reports.** Go to Import, choose the file type, and upload. Check the preview, then confirm.
4. 🔎 **Needs review.** Rows with no matching fund or category rule land under Transactions → Needs review. Add the fund or a keyword rule and click *Re-run matching*, or assign rows in bulk.
5. 🗓️ **Plan.** Add appointments for people paid from each fund (start and end, monthly salary and effort %, or hourly rate and hours per week), and Pending entries for known upcoming costs.
6. 📈 **Watch the dashboard.** Alerts, projected balances, burn rate, and runway for each fund. Each fund page has a spending chart and a CSV export.

<details>
<summary><b>📥 Importing reports: details and gotchas</b></summary>

<br>

- The first time you import a layout, match the columns: date, amount, worktag columns, person, status, and so on.
- Rows with statuses like Draft or Canceled are skipped by default. If a report you imported earlier now shows a skipped status (cancelled, sent back), importing the newer file removes the earlier transaction so it stops counting.
- Total and subtotal rows are skipped automatically.
- For a worksheet with one column per month, select all of the month columns as "Amount".
- The preview shows what will be imported, what's a duplicate, and which worktag IDs you don't track yet.
- Re-import cumulative exports (for example all expense reports year-to-date) as often as you like: rows already in the ledger are skipped. One caveat: if a report's *amount* changes between exports (a line edited before approval), the new amount is imported as a new row, so delete the old one under Transactions.

</details>

<details>
<summary><b>🧮 How projections work</b></summary>

<br>

| Piece | Rule |
|---|---|
| Actuals | Imported and manual transactions. Uncategorized amounts count as "Other" until you fix them. |
| Funding | A fund's budget is the sum of its funding entries. State funds count only the current fiscal year's allocation and spending, since unspent money lapses on June 30. |
| Rates | Fringe and tuition come from the person type's rate for the fiscal year (July–June) each month falls in. A missing year reuses the closest earlier year and shows an alert. |
| Fringe on actuals | Payroll salary with no fringe line for that person and month gets fringe estimated at that month's fiscal-year rate. |
| Planned payroll | Each appointment month *without* a salary actual for that person on that fund: salary × effort (prorated for partial months), plus fringe. |
| Hourly | Hourly rate × hours per week × 52 ÷ 12 per month, plus fringe at the person type's rate. |
| Tuition | That fiscal year's monthly tuition remission × effort, for months without a tuition actual. |
| Pending | Pending entries count until they're reconciled or cancelled. |
| F&A | Fund rate × spending in categories marked "Subject to F&A". It applies to actual, pending, and planned amounts. |
| Burn rate | Average monthly actual spending over the last 6 complete months (fewer if the fund or its spending started more recently). Runway = (budget − spent − pending) ÷ burn rate. |

Known simplifications: the $25k-per-subaward MTDC cap and multi-year budget periods aren't modeled; each fund has a single F&A rate; payroll actuals count in the month of the row's date (usually the pay period end).

</details>

## 🐳 Deploy

The `Dockerfile` builds a production image: gunicorn with threaded workers, static files served by WhiteNoise, and migrations run at startup. Every release is also published as a ready-made image at `ghcr.io/sustainableurbansystemslab/facultyfunds` (amd64 and arm64).

Production mode sets secure-only cookies and redirects to HTTPS, so **put a TLS-terminating reverse proxy in front** (login fails over plain HTTP). Health checks can use `/healthz`.

<details>
<summary><b>🐋 Docker Compose (any Docker host, including a NAS)</b></summary>

<br>

`docker-compose.yml` runs the app plus Postgres, with data in a named volume.

1. Copy the project to the host.
2. Create your secrets file. `.env` is git-ignored and excluded from the image:

   ```bash
   cp .env.docker.example .env
   # fill in POSTGRES_PASSWORD, DJANGO_SECRET_KEY, PII_HMAC_KEY and your hostname
   ```

3. Start it and create your account:

   ```bash
   docker compose up -d --build
   docker compose exec web python manage.py createsuperuser
   ```

4. Point your reverse proxy at `127.0.0.1:8020` (the app listens on loopback only), open the site, and enroll 2FA right away.

**Updating:** `docker compose pull && docker compose up -d` for the prebuilt image (pin a release with `IMAGE_TAG=1.2.3`), or `docker compose up -d --build` to build on the host. Containers labelled for [Watchtower](https://containrrr.dev/watchtower/) update themselves when a new image is published.

**Backups** (keep dumps out of the repo; `*.sql`, `*.dump` and `backups/` are git-ignored):

```bash
docker compose exec -T db pg_dump -U facultyfunds facultyfunds | gzip > backups/ff-$(date +%F).sql.gz
```

Also back up `.env` (especially `PII_HMAC_KEY`) separately from the database dump.

</details>

<details>
<summary><b>☁️ Cloud host (Render, Fly.io, Railway, Azure App Service)</b></summary>

<br>

Use a managed **Postgres** database, since container disks are usually wiped on redeploy. Set these environment variables (see `.env.example`):

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | long random string |
| `PII_HMAC_KEY` | long random string. **Keep it stable and back it up**: if it changes, existing people won't be recognized in new imports |
| `DATABASE_URL` | `postgres://…` |
| `DJANGO_ALLOWED_HOSTS` | your hostname, e.g. `funds.example.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://funds.example.com` |
| `AXES_PROXY_COUNT` | `1` behind the host's load balancer, so login lockouts use your real IP |

Then create your account from the host's shell and enroll 2FA right away:

```bash
python manage.py createsuperuser
```

The Django admin is at `/admin/` (change it with `DJANGO_ADMIN_URL`) and also requires 2FA. Turn on your database provider's automatic backups: losing the database means re-entering budgets and plans, though transactions can be re-imported.

</details>

<details>
<summary><b>⚙️ Optional settings</b></summary>

<br>

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_ADMIN_URL` | `admin/` | Where the Django admin lives |
| `SESSION_IDLE_SECONDS` | `7200` | Sign out after this long without activity |
| `TIME_ZONE` | `America/New_York` | Used for dates and fiscal-year boundaries |
| `WEB_CONCURRENCY` | `2` | gunicorn worker processes |
| `GUNICORN_THREADS` | `4` | Threads per worker |
| `SLOW_REQUEST_SECONDS` | `1` | Log requests slower than this, with their database time |

</details>

## 🔐 Security and privacy

- 🔑 Single owner account, password plus authenticator-app 2FA with backup codes, login lockout, strict security headers, and no page caching.
- 🪪 Passkeys: add one under **Security** after your first sign-in. A passkey signs you in on its own (it needs your device's fingerprint, face or PIN) or replaces the 6-digit code after a password. Passkeys are tied to the site's domain from `DJANGO_ALLOWED_HOSTS` and need HTTPS (or `http://localhost` in development); set `PASSKEY_RP_ID` / `PASSKEY_ORIGINS` only if the site has several names. Password managers that store passkeys, such as Bitwarden or 1Password, work too.
- 🕵️ Names and employee IDs are replaced by initials and a keyed hash (HMAC) the moment a file is read. Real names are never stored in the database.

<details>
<summary><b>More security notes</b></summary>

<br>

- Until 2FA is set up, the password alone can enroll an authenticator. Enroll immediately after `createsuperuser`.
- Lost your phone and backup codes? Run `python manage.py reset_2fa <username>` from the host shell, then enroll again right away.
- After 5 failed logins from the same IP for the same username, logins are locked for 1 hour. Sessions end after 2 hours idle or when the browser closes.
- Uploaded files are read in memory and never written to disk; requests over the size limit are refused before parsing. Staged import data (already pseudonymized) is deleted when you confirm or cancel, or after 24 hours.
- Names in columns the app doesn't recognize as people columns are only pseudonymized once you map that column as "Person". Names embedded in free-text cells are masked only when the same row also has a recognized name column.

</details>

## 🏷️ Releases and versions

Versions follow [semantic versioning](https://semver.org) and are created **automatically** from commit messages written in the [Conventional Commits](https://www.conventionalcommits.org) style. Nothing is edited by hand: on every push to `main`, CI runs the tests, works out the next version, tags it, publishes a GitHub release with notes, and builds the Docker image with that version baked in. The running app shows its version in the page footer.

| Commit message | Release |
|---|---|
| `fix: …` or `perf: …` | patch, 1.2.3 → 1.2.4 |
| `feat: …` | minor, 1.2.3 → 1.3.0 |
| `feat!: …` or a `BREAKING CHANGE:` footer | major, 1.2.3 → 2.0.0 |
| `docs:`, `style:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:` | no release on their own |

<details>
<summary><b>How it works, and how to steer it</b></summary>

<br>

- **Git tags are the only source of truth.** `scripts/release.py` finds the latest `vX.Y.Z` tag, reads the commits since, and picks the biggest bump. Nothing is committed back to `main`, so there is no release commit and no loop.
- **Image tags:** each release publishes `X.Y.Z`, `X.Y`, and `latest`; every build also gets a `sha-…` tag. Merges that don't warrant a release (docs, chores) update `latest` and show as `X.Y.Z+N` in the footer, meaning N commits after the last release.
- **Where the footer gets its version:** the Docker image gets `APP_VERSION`/`APP_COMMIT` from CI. A plain checkout asks `git describe`. A `git archive` or zip download carries a `VERSION` file that git fills in. Anything else says `dev`.
- **Commits that don't follow the style** are never an error, they just don't trigger a release. With squash merges, the pull request title is what counts.
- **Preview what the next release would be:**

  ```bash
  python3 scripts/release.py
  ```

- **Start counting from 1.0.0** (or any version) by tagging by hand; later releases continue from it:

  ```bash
  git tag v1.0.0 && git push origin v1.0.0
  ```

</details>

## 🛠️ Development

```bash
uv run python manage.py test
```

<details>
<summary><b>Useful commands and code layout</b></summary>

<br>

| Command | What it does |
|---|---|
| `seed_demo` | Example data and sample import files |
| `check_speed` | Times the main pages as the owner and shows how much is database time. Run it on the server when pages feel slow |
| `reset_2fa <username>` | Clears a user's authenticator so they can enroll again |

Code layout: `accounts/` handles login and 2FA. `ledger/services/` holds the file parsing, pseudonymization, column mapping, import pipeline, and projections. Views and templates live in `ledger/`. `scripts/release.py` computes versions, and `config/version.py` tells the running app which one it is.

</details>

<details>
<summary><b>🔬 Tuning for your real exports</b></summary>

<br>

The column matching was built from public Workday and OneUSG documentation, not from real files. To fit the importer to your exact layouts, put an export in `samples/` (git-ignored) and ask for the parsers to be tuned. Blank out names or amounts first if you like.

</details>
