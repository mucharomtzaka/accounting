from django.core.management.base import BaseCommand

from core.models import Company
from notify.scanner import SCANNERS, scan


class Command(BaseCommand):
    help = 'Scan kondisi financeira dan buat notification (PRD 33)'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='company_code; default semua company aktif')
        parser.add_argument(
            '--only', nargs='*', choices=[name for name, _ in SCANNERS],
            help='Jalankan scanner tertentu saja',
        )

    def handle(self, *args, **options):
        if options['company']:
            companies = Company.objects.filter(
                company_code=options['company'], status='ACTIVE')
        else:
            companies = Company.objects.filter(status='ACTIVE')

        if not companies.exists():
            self.stdout.write(self.style.WARNING('Tidak ada company aktif.'))
            return

        total = 0
        for company in companies:
            results = scan(company, only=options['only'])
            created = sum(len(v) for k, v in results.items() if not k.endswith(':error'))
            errors = [f'{k}: {v[0]}' for k, v in results.items() if k.endswith(':error')]
            total += created
            self.stdout.write(f'{company.company_code}: {created} notification baru')
            for detail in results.items():
                if not detail[0].endswith(':error') and detail[1]:
                    for n in detail[1]:
                        self.stdout.write(f'  [{n.get_severity_display()}] {n.title}')
            for err in errors:
                self.stdout.write(self.style.ERROR(f'  scanner error - {err}'))

        self.stdout.write(self.style.SUCCESS(f'Total {total} notification dibuat.'))