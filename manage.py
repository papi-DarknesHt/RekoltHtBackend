#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # voir BackendRekoltHt/asgi.py pour le détail : évite qu'un print()
    # contenant un caractère hors cp1252 (page de code par défaut sur
    # Windows) ne plante une commande manage.py en pleine exécution.
    for _flux in (sys.stdout, sys.stderr):
        if hasattr(_flux, 'reconfigure'):
            _flux.reconfigure(encoding='utf-8', errors='replace')

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BackendRekoltHt.settings.dev')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
