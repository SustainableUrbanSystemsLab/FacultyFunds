# Faculty Budget

A private web app for tracking your own faculty funds when you don't have direct access to the balances. It is built around **Workday** (expense reports, worktags) and **OneUSG Connect** (PeopleSoft payroll) exports.

- **Funds:** track grants, gifts, cost centers, projects, and programs by worktag ID. Each has a budget per category, start and end dates, and an F&A rate.
- **Imports:** upload Workday expense report exports and OneUSG payroll worksheets (`.xlsx`, `.xls`, `.csv`, or PeopleSoft "Excel" downloads that are really HTML). The app finds the header row and suggests a column mapping. You confirm a preview before anything is saved. Mappings are remembered, duplicate rows are skipped when you re-import, and any import can be undone.
- **Budget vs. actuals:** see spent, pending, planned payroll, and what's left, now and projected to the end date.
- **Payroll projections:** each appointment is monthly salary × effort, plus fringe from the person type's rate, graduate tuition remission per month, and summer salary.
- **F&A:** calculated from each fund's rate on MTDC categories, or taken from imported F&A lines.
- **Burn rate and runway:** alerts for overspending, funds running out before the end date, funds ending with money unspent, and stale pending items.
- **Pending entries:** log expected spending, then reconcile it with the real transaction once it's imported.
- **Security:** single owner account, password plus authenticator-app 2FA with backup codes, login lockout, strict security headers, and no page caching.
- **Privacy:** names and employee IDs are replaced by initials and a keyed hash (HMAC) the moment a file is read. Real names are never stored in the database.

> **Data policy:** payroll exports contain other people's salary data. Before you put real data on a cloud host, check with your department IT or data steward that the host is an approved place for it.

## Run it locally

Requires [uv](https://docs.astral.sh/uv/).

Copy `.env.example` to `.env`, set `DJANGO_DEBUG=1`, and fill in `DJANGO_SECRET_KEY` and `PII_HMAC_KEY` with long random strings.

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open http://127.0.0.1:8000, sign in, and scan the QR code with an authenticator app. **Save the backup codes** that appear next.

To try it with fake data, run `uv run python manage.py seed_demo`. It adds two example funds and an appointment, and writes sample Workday and OneUSG files to `samples/demo/` for you to upload on the Import page.

## Typical workflow

1. **Add your funds:** go to Funds → Add fund. Enter the worktag ID exactly as Workday shows it (e.g. `GR00012345`). Under "Other IDs", add the award ID (`AWD…`) or OneUSG project code so payroll rows match too. Then enter the budget per category and the F&A rate.
2. **Set rates:** under Settings → Fringe and tuition rates by fiscal year, enter each year's fringe rate and monthly tuition remission for each person type. Fiscal years run July–June, so FY2027 is July 2026 to June 2027. A year left blank reuses the closest earlier year. The defaults are Georgia Tech's FY2025 values.
3. **Import reports:** go to Import, choose the file type, and upload.
   - The first time you import a layout, match the columns: date, amount, worktag columns, person, status, and so on.
   - Rows with statuses like Draft or Canceled are skipped by default. If a report you imported earlier now shows a skipped status (cancelled, sent back), importing the newer file removes the earlier transaction so it stops counting.
   - Total and subtotal rows are skipped automatically.
   - For a worksheet with one column per month, select all of the month columns as "Amount".
   - The preview shows what will be imported, what's a duplicate, and which worktag IDs you don't track yet.
   - Re-import cumulative exports (e.g. all expense reports year-to-date) as often as you like: rows already in the ledger are skipped. One caveat: if a report's *amount* changes between exports (a line edited before approval), the new amount is imported as a new row, so delete the old one under Transactions.
4. **Review:** rows with no matching fund or category rule land in Review. Add the fund or a keyword rule and click "Re-run matching", or assign rows in bulk.
5. **Plan:** add appointments for people paid from each fund (start and end, monthly salary, effort %), and pending entries for known upcoming costs.
6. **Watch the dashboard:** it shows alerts, projected balances, burn rate, and runway for each fund. Each fund page has a spending chart and a CSV export.

### How projections work

| Piece | Rule |
|---|---|
| Actuals | Imported and manual transactions. Uncategorized amounts count as "Other" until you fix them. |
| Rates | Fringe and tuition come from the person type's rate for the fiscal year (July–June) each month falls in. A missing year reuses the closest earlier year and shows an alert. |
| Fringe on actuals | Payroll salary with no fringe line for that person and month gets fringe estimated at that month's fiscal-year rate. |
| Planned payroll | Each appointment month *without* a salary actual for that person on that fund: salary × effort (prorated for partial months), plus fringe. |
| Tuition | That fiscal year's monthly tuition remission × effort, for months without a tuition actual. |
| Pending | Pending entries count until they're reconciled or cancelled. |
| F&A | Fund rate × spending in categories marked "Subject to F&A". It applies to actual, pending, and planned amounts. |
| Burn rate | Average monthly actual spending over the last 6 complete months (fewer if the fund or its spending started more recently). Runway = (budget − spent − pending) ÷ burn rate. |

Known simplifications: the $25k-per-subaward MTDC cap and multi-year budget periods aren't modeled; each fund has a single F&A rate; payroll actuals count in the month of the row's date (usually the pay period end).

## Deploy to a cloud host

The `Dockerfile` builds a production image: gunicorn, static files served by WhiteNoise, and migrations run at startup. It works on Render, Fly.io, Railway, Azure App Service, and similar hosts. Use a managed **Postgres** database, since container disks are usually wiped on redeploy.

Set these environment variables (see `.env.example`):

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | long random string |
| `PII_HMAC_KEY` | long random string. **Keep it stable and back it up**: if it changes, existing people won't be recognized in new imports |
| `DATABASE_URL` | `postgres://…` |
| `DJANGO_ALLOWED_HOSTS` | your hostname, e.g. `budget.example.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://budget.example.com` |
| `AXES_PROXY_COUNT` | `1` behind the host's load balancer, so login lockouts use your real IP |

Then create your account from the host's shell and enroll 2FA right away:

```bash
python manage.py createsuperuser
```

Health checks can use `/healthz`. The Django admin is at `/admin/` (change it with `DJANGO_ADMIN_URL`) and also requires 2FA.

**Backups:** turn on your database provider's automatic backups. Losing the database means re-entering budgets and plans. Transactions can be re-imported.

## Deploy on a NAS (Docker Compose)

Works on Synology (Container Manager), QNAP (Container Station), Unraid, TrueNAS, or any Docker host. `docker-compose.yml` runs the app plus Postgres, with data in a named volume.

1. Get the code onto the NAS (`git clone` over SSH, or copy the folder).
2. Create your secrets file. `.env` is git-ignored and excluded from the image:

   ```bash
   cp .env.nas.example .env
   # fill in POSTGRES_PASSWORD, DJANGO_SECRET_KEY, PII_HMAC_KEY, hostname
   ```

3. Start it:

   ```bash
   docker compose up -d --build
   docker compose exec web python manage.py createsuperuser
   ```

4. **Put HTTPS in front (Synology DSM).** Production mode sets secure-only cookies and redirects to HTTPS, so login fails over plain HTTP. The app listens on `localhost:8000` only.
   - Cloudflare DNS: add `funds` pointing at your home's public IP. For the Let's Encrypt step set it to **DNS only** (grey cloud) so validation reaches the NAS; you can switch to Proxied afterwards (SSL mode Full).
   - Router: forward 80 and 443 to the NAS (DSM's nginx owns them).
   - DSM > Control Panel > Security > Certificate > Add > Let's Encrypt for `funds.sustainableurbansystems.com`.
   - DSM > Control Panel > Login Portal > Advanced > Reverse Proxy > Create: source HTTPS `funds.sustainableurbansystems.com` port 443, destination HTTP `localhost` port 8000. Assign the certificate to this rule under Certificate > Settings. DSM sends `X-Forwarded-Proto`, which the app needs.
   - This exposes the login page to the internet. The app has 2FA and lockouts, but consider Cloudflare Access (email allow-list) in front, or a VPN instead.
5. Open the site and enroll 2FA right away.

**Backups** (Postgres dump, keep it off the repo; `*.sql`, `*.dump` and `backups/` are git-ignored):

```bash
docker compose exec -T db pg_dump -U facultybudget facultybudget | gzip > backups/fb-$(date +%F).sql.gz
```

Also back up `.env` (especially `PII_HMAC_KEY`) somewhere safe, separate from the database dump. Update with `git pull && docker compose up -d --build`.

### Security notes

- Until 2FA is set up, the password alone can enroll an authenticator. Enroll immediately after `createsuperuser`.
- Lost your phone and backup codes? Run `python manage.py reset_2fa <username>` from the host shell, then enroll again right away.
- After 5 failed logins from the same IP for the same username, logins are locked for 1 hour. Sessions end after 2 hours idle or when the browser closes.
- Uploaded files are read in memory and never written to disk; requests over the size limit are refused before parsing. Staged import data (already pseudonymized) is deleted when you confirm or cancel, or after 24 hours.
- Names in columns the app doesn't recognize as people columns are only pseudonymized once you map that column as "Person". Names embedded in free-text cells are masked only when the same row also has a recognized name column.

## Development

```bash
uv run python manage.py test
```

Useful commands: `seed_demo` (example data and sample files), `reset_2fa <username>`.

Code layout: `accounts/` handles login and 2FA. `ledger/services/` holds the file parsing, pseudonymization, column mapping, import pipeline, and projections. Views and templates live in `ledger/`.

## Tuning for your real exports

The column matching was built from public Workday and OneUSG documentation, not from real files. To fit the importer to your exact layouts, put an export in `samples/` (git-ignored) and ask for the parsers to be tuned. Blank out names or amounts first if you like.
