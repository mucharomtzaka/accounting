# Deploy ke VPS aaPanel — Python Project + Domain

Target: Django 4.2 (`config/wsgi.py` → `config.wsgi:application`), MySQL/MariaDB via PyMySQL, static di `staticfiles/`.

## 1. Persiapan

1. Domain: buat A record `@` dan `www` → IP VPS. Tunggu resolve (`ping domainanda.com`).
2. aaPanel: pastikan terpasang **App Store → Python Manager** (Python 3.11 atau 3.13), **Database → MySQL/MariaDB**, **Website → SSL (Let's Encrypt)**.
3. Buat database: aaPanel **Database → Add Database**. Catat `DB_NAME`, `DB_USER`, `DB_PASSWORD`. Host `127.0.0.1`, port `3306`.

## 2. Upload project

Path contoh: `/www/wwwroot/akuntansi.domainanda.com`

Upload via git atau zip. Yang **jangan** diupload: `.venv/`, `__pycache__/`, `.env`, `db.sqlite3`, `tailwindcss` (binary macOS, tidak jalan di Linux), `node_modules/`.

```bash
cd /www/wwwroot/akuntansi.domainanda.com
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt gunicorn
```

`requirements.txt` belum memuat `gunicorn`, jadi install eksplisit seperti di atas (jangan lupa restart project setelahnya kalau ganti versi).

## 3. Buat Python Project di aaPanel

**Website → Python Project → Add Project**, isi:

| Field aaPanel | Isi |
|---|---|
| Project path | `/www/wwwroot/akuntansi.domainanda.com` |
| Python version | 3.11+ (sesuai venv) |
| Framework | Django |
| Startup mode / command | `gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3` |
| Port | `8000` (bebas, yang penting sama dengan `--bind`) |
| Domain mapping | `akuntansi.domainanda.com` (aaPanel membuat reverse-proxy nginx ke port di atas) |

Jalankan dengan user non-root bila memungkinkan; pastikan user itu punya hak tulis ke `media/` dan `staticfiles/`.

## 4. File `.env` produksi

Salin dari contoh, lalu ubah:

```bash
cp .env.example .env
nano .env
```

```ini
SECRET_KEY=<isi-50-karakter-acak>
DEBUG=False
ALLOWED_HOSTS=akuntansi.domainanda.com,www.akuntansi.domainanda.com
DB_ENGINE=django.db.backends.mysql
DB_NAME=akuntansi_db
DB_USER=akuntansi_user
DB_PASSWORD=<password-kuat>
DB_HOST=127.0.0.1
DB_PORT=3306
```

Generate key:

```bash
.venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## 5. Wajib: `CSRF_TRUSTED_ORIGINS`

Tanpa ini login admin & semua POST lewat HTTPS gagal (403 CSRF). Tambahkan di `config/settings.py`:

```python
CSRF_TRUSTED_ORIGINS = ["https://akuntansi.domainanda.com", "https://www.akuntansi.domainanda.com"]
```

## 6. Build CSS, migrate, static

Binary `tailwindcss` di repo adalah build **macOS ARM** — di VPS Linux harus unduh ulang:

```bash
cd /www/wwwroot/akuntansi.domainanda.com
curl -sL -o tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64
chmod +x tailwindcss
./tailwindcss --minify -i core/static_src/css/styles.css -o static/css/dist/styles.css
.venv/bin/python manage.py migrate --no-input
.venv/bin/python manage.py collectstatic --no-input
.venv/bin/python manage.py createsuperuser   # atau: seed_data --demo untuk demo
.venv/bin/python manage.py check
```

`static/css/dist/` masuk `.gitignore`, jadi langkah build CSS wajib tiap deploy bersih.

## 7. Static, media, SSL

1. Python Project mapping otomatis melayani Django; pastikan di reverse-proxy nginx aaPanel ada mapping:
   - URL `/static/` → `/www/wwwroot/akuntansi.domainanda.com/staticfiles/`
   - URL `/media/` → `/www/wwwroot/akuntansi.domainanda.com/media/`
   - Kalau tidak ada menunya, buat **Website → domain → Static / Proxy** manual.
2. **Website → SSL → Let's Encrypt** → Apply + **Force HTTPS**.
3. Start/Restart Python Project di aaPanel, buka `https://akuntansi.domainanda.com/admin/`.

## 8. Update berikutnya

```bash
cd /www/wwwroot/akuntansi.domainanda.com
git pull  # atau upload ulang
.venv/bin/pip install -r requirements.txt
./tailwindcss --minify -i core/static_src/css/styles.css -o static/css/dist/styles.css
.venv/bin/python manage.py migrate --no-input
.venv/bin/python manage.py collectstatic --no-input
# lalu Restart dari panel Python Project aaPanel
```

## 9. Troubleshooting

| Gejala | Periksa |
|---|---|
| 502 / project tidak jalan | Log Python Project aaPanel; `gunicorn` terinstall? port `--bind` = port panel? `.venv` milik user yang benar? |
| `Access denied` DB | `DB_USER`/`DB_PASSWORD`/`DB_HOST` di `.env`; user DB diberi hak ke `DB_NAME` (`%` atau `127.0.0.1`) |
| CSS admin hilang | `collectstatic` sudah jalan? mapping `/static/` → `staticfiles/` benar? |
| Upload gagal / 413 | `DATA_UPLOAD_MAX_MEMORY_SIZE` 20 MB; file restore > 20 MB ditolak view backup |
| 403 CSRF saat login | `CSRF_TRUSTED_ORIGINS` belum diisi domain `https://` |
| Redirect HTTP, bukan HTTPS | Aktifkan Force HTTPS + `ALLOWED_HOSTS` berisi domain final |
