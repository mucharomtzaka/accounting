"""Report HTML -> screenshot-free content check. Pastikan KPI benar-benar berisi angka."""
import os
import re

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse

from core.models import User

STRIP = re.compile(r'<[^>]+>|\s+')


def text(html):
    return STRIP.sub(' ', html).strip()


def numbers(html):
    return re.findall(r'[\d][\d.,]{2,}', STRIP.sub(' ', html))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
def main():
    client = Client()
    client.force_login(User.objects.get(username='admin'))

    targets = [
        ('Dashboard', reverse('reports_dashboard')),
        ('Trial Balance', reverse('reports_trial_balance')),
        ('P&L', reverse('reports_profit_loss')),
        ('Balance Sheet', reverse('reports_balance_sheet')),
        ('Aging', reverse('reports_aging')),
        ('Comparative Balance', reverse('reports_balance_comparison')),
        ('Company Comparison', reverse('reports_company_comparison')),
    ]
    failures = 0
    for name, url in targets:
        html = client.get(url).content.decode()
        found = numbers(html)
        css = client.get('/static/css/dist/styles.css')
        print(f'{name:22} angka ditemukan: {len(found):3} | css: {css.status_code} | sample: {found[:6]}')
        if not found:
            print(f'  [FAIL] {name} tidak menampilkan nilai numerik')
            failures += 1
    print()
    print('PASS' if not failures else f'FAIL ({failures})')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())