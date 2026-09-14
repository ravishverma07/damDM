#!/usr/bin/env bash
# Convenience entrypoints for local development.
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-help}" in
  setup)   .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_plans ;;
  run)     .venv/bin/python manage.py runserver ;;
  worker)  .venv/bin/celery -A config worker -l info ;;
  beat)    .venv/bin/celery -A config beat -l info ;;
  test)    .venv/bin/python -m pytest "${@:2}" ;;
  lint)    .venv/bin/ruff check . ;;
  check)   DJANGO_SETTINGS_MODULE=config.settings.prod .venv/bin/python manage.py check --deploy ;;
  *)       echo "usage: scripts/dev.sh {setup|run|worker|beat|test|lint|check}" ;;
esac
