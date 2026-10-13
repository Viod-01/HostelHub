#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py createsuperuser --noinput || echo "NOTE: DJANGO_SUPERUSER_USERNAME/_EMAIL/_PASSWORD not all set - no admin created on this deploy"
python manage.py seed_rooms