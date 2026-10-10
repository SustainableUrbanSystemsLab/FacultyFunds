"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()


def warm_up() -> None:
    """Import every view and compile the main templates before the first visitor arrives.

    On a busy disk the first request after a restart otherwise spends seconds reading Python modules and
    templates. Nothing here touches the database.
    """
    from django.template.loader import get_template
    from django.urls import get_resolver

    get_resolver().url_patterns  # imports the URL configuration, and with it every view, form and service
    for name in ("base.html", "accounts/login.html", "ledger/dashboard.html", "ledger/fund_detail.html",
                 "ledger/transaction_list.html", "ledger/people_list.html", "ledger/settings.html"):
        get_template(name)


try:
    warm_up()
except Exception:  # a failed warm-up must never stop the app from starting
    import logging

    logging.getLogger("facultyfunds").exception("warm-up failed")
