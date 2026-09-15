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

A git-ignored `.env` with `DJANGO_DEBUG=1` and random local keys is already in place. On a fresh clone, copy `.env.example` to `.env` and fill in the keys.

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
2. **Set rates:** under Settings, check the fringe rates and monthly tuition remission for each person type. The defaults are Georgia Tech's FY2025 values, so verify the current ones.
3. **Import reports:** go to Import, choose the file type, and upload.
   - The first time you import a layout, match the columns: date, amount, worktag columns, person, status, and so on.
   - Rows with statuses like Draft or Canceled are skipped by default.
   - For a worksheet with one column per month, select all of the month columns as "Amount".
   - The preview shows what will be imported, what's a duplicate, and which worktag IDs you don't track yet.
4. **Review:** rows with no matching fund or category rule land in Review. Add the fund or a keyword rule and click "Re-run matching", or assign rows in bulk.
5. **Plan:** add appointments for people paid from each fund (start and end, monthly salary, effort %), and pending entries for known upcoming costs.
6. **Watch the dashboard:** it shows alerts, projected balances, burn rate, and runway for each fund. Each fund page has a spending chart and a CSV export.

### How projections work

| Piece | Rule |
|---|---|
| Actuals | Imported and manual transactions. Uncategorized amounts count as "Other" until you fix them. |
| Fringe on actuals | Payroll salary with no fringe line for that person and month gets fringe estimated from their rate. |
| Planned payroll | Each appointment month *without* a salary actual for that person on that fund: salary × effort (prorated for partial months), plus fringe. |
| Tuition | Monthly tuition remission × effort, for months without a tuition actual. |
| Pending | Pending entries count until they're reconciled or cancelled. |
| F&A | Fund rate × spending in categories marked "Subject to F&A". It applies to actual, pending, and planned amounts. |
| Burn rate | Average monthly actual spending over the last 6 complete months. Runway = (budget − spent − pending) ÷ burn rate. |

Known simplifications: the $25k-per-subaward MTDC cap and multi-year budget periods aren't modeled, and fringe rates don't change by fiscal year.

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

### Security notes

- Until 2FA is set up, the password alone can enroll an authenticator. Enroll immediately after `createsuperuser`.
- Lost your phone and backup codes? Run `python manage.py reset_2fa <username>` from the host shell, then enroll again right away.
- After 5 failed logins from the same IP for the same username, logins are locked for 1 hour. Sessions end after 2 hours idle or when the browser closes.
- Uploaded files are read in memory and never written to disk. Staged import data is deleted when you confirm or cancel, or after 24 hours.

## Development

```bash
uv run python manage.py test
```

Useful commands: `seed_demo` (example data and sample files), `reset_2fa <username>`.

Code layout: `accounts/` handles login and 2FA. `ledger/services/` holds the file parsing, pseudonymization, column mapping, import pipeline, and projections. Views and templates live in `ledger/`.

## Tuning for your real exports

The column matching was built from public Workday and OneUSG documentation, not from real files. To fit the importer to your exact layouts, put an export in `samples/` (git-ignored) and ask for the parsers to be tuned. Blank out names or amounts first if you like.
