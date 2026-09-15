"""Synthetic example exports (fake people and amounts) for tests, demos, and trying the importer."""

import datetime as dt
import io

import openpyxl

WORKDAY_ROWS = [
    # Expense Report, Worker, Date, Expense Item, Memo, Worktags, Total Amount, Status
    ("EXP-0012345 - Jane Doe on 08/20/2026 ($1,101.60)", "Jane Doe", dt.datetime(2026, 8, 20), "Airfare",
     "Flight to ASHRAE conference", "Grant: GR00012345 Solar Facade Study\nCost Center: CC0001234 School of Architecture",
     412.50, "Approved"),
    ("EXP-0012345 - Jane Doe on 08/20/2026 ($1,101.60)", "Jane Doe", dt.datetime(2026, 8, 20), "Lodging",
     "Hotel, 3 nights", "Grant: GR00012345 Solar Facade Study\nCost Center: CC0001234 School of Architecture",
     689.10, "Approved"),
    ("EXP-0012388 - John Smith on 08/28/2026 ($120.00)", "John Smith", dt.datetime(2026, 8, 28), "Lab Supplies",
     "Sensors for test cell", "Grant: GR00012345 Solar Facade Study", 120.00, "Paid"),
    ("EXP-0012390 - John Smith on 09/02/2026 ($75.00)", "John Smith", dt.datetime(2026, 9, 2), "Meals",
     "Draft, not submitted", "Grant: GR00012345 Solar Facade Study", 75.00, "Draft"),
    ("EXP-0012401 - Jane Doe on 09/05/2026 ($55.00)", "Jane Doe", dt.datetime(2026, 9, 5), "Meals",
     "Visitor lunch", "Gift: GF00077777 Lab Discretionary", 55.00, "Approved"),
]


def workday_expense_xlsx(rows=None) -> bytes:
    """Looks like a Workday 'Export to Excel': title rows, then a header row, then a total row."""
    rows = WORKDAY_ROWS if rows is None else rows
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Find Expense Reports"
    sheet.append(["Find Expense Reports"])
    sheet.append(["Company", "Georgia Institute of Technology"])
    sheet.append([])
    sheet.append(["Expense Report", "Worker", "Expense Report Date", "Expense Item", "Memo", "Worktags",
                  "Total Amount", "Expense Report Status"])
    for row in rows:
        sheet.append(list(row))
    sheet.append(["Total", "", "", "", "", "", sum(r[6] for r in rows), ""])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


ONEUSG_ROWS = [
    # Pay Run ID, Emp ID, Name, Acct, Descr, Project, Expended Date, Amount
    ("M26AUG", "000123456", "Doe,Jane A", "Salaries - Graduate Assistant", "Architecture", "GR00012345", "08/31/2026", "2,500.00"),
    ("M26AUG", "000654321", "Smith,John", "Salaries - Postdoc", "Architecture", "GR00012345", "08/31/2026", "5,000.00"),
    ("M26AUG", "000654321", "Smith,John", "Fringe Benefits", "Architecture", "GR00012345", "08/31/2026", "1,585.00"),
    ("M26AUG", "000777888", "Lee,Ann", "Salaries - Student Hourly", "Architecture", "CC0001234", "08/31/2026", "640.00"),
]


def oneusg_payroll_html() -> bytes:
    """Looks like a PeopleSoft 'Download to Excel' file: an HTML table saved as .xls with a title row."""
    header = ["Pay Run ID", "Emp ID", "Name", "Acct", "Descr", "Project", "Expended Date", "Amount"]
    rows = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in ONEUSG_ROWS)
    html = (
        "<html><head><meta charset='utf-8'></head><body><table border='1'>"
        f"<tr><td colspan='{len(header)}'>Payroll Expenses Detail - 180_CA_PERS_SERV_BOR</td></tr>"
        "<tr>" + "".join(f"<th>{h}</th>" for h in header) + "</tr>"
        f"{rows}</table></body></html>"
    )
    return html.encode("utf-8")
