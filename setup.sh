#!/bin/bash
# Setup proyek dari nol. Jalankan: bash setup.sh
set -e
cd "$(dirname "$0")"

echo "==> 1. Virtual environment"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip -q install --upgrade pip
./.venv/bin/pip -q install -r requirements.txt

echo "==> 2. Env file"
[ -f .env ] || cp .env.example .env
echo "    .venv/bin/python manage.py --help >/dev/null && echo '    .env OK'"

echo "==> 3. Tailwind CLI (binary standalone, tanpa npm install)"
if ! ./tailwindcss --help >/dev/null 2>&1; then
  curl -sL -o tailwindcss \
    https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-macos-arm64
  chmod +x tailwindcss
fi
./tailwindcss --help >/dev/null && echo "    tailwindcss OK"

echo "==> 4. Build CSS"
./tailwindcss --minify -i core/static_src/css/styles.css -o static/css/dist/styles.css 2>/dev/null
echo "    static/css/dist/styles.css OK"

echo "==> 5. Database"
# XAMPP MariaDB harus sudah jalan. Cek koneksi.
./.venv/bin/python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
print('    DB koneksi OK:', connection.settings_dict['HOST'], connection.settings_dict['NAME'])
" || {
  echo "    GAGAL konek DB. Pastikan XAMPP MySQL/MariaDB berjalan."
  echo "    Buat database dulu: mysql -u root -e \"CREATE DATABASE accounting CHARACTER SET utf8mb4;\""
  exit 1
}

echo "==> 6. Migrate"
./.venv/bin/python manage.py migrate --noinput

echo "==> 7. Seed data"
./.venv/bin/python manage.py seed_data

echo "==> 8. Collectstatic"
./.venv/bin/python manage.py collectstatic --noinput >/dev/null

echo
echo "Selesai. Jalankan:"
echo "  source .venv/bin/activate"
echo "  python manage.py runserver"
echo
echo "Login: http://127.0.0.1:8000/admin/  (admin / admin123)"
echo "Reports: http://127.0.0.1:8000/admin/reports/"
echo
echo "Development:"
echo "  ./tailwindcss --watch -i core/static_src/css/styles.css -o static/css/dist/styles.css"
echo "  python smoke_test.py     # accounting rules (34 checks)"
echo "  python smoke_http.py     # semua halaman render 200 (36 checks)"