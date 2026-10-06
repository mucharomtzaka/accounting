"""Tag untuk menampilkan daftar akun demo di halaman login.

Sumbernya `seed_demo_users` (USER_PROFILE + DEMO_ROLES + DEFAULT_PASSWORD) supaya
username, label role, dan password yang ditampilkan tidak pernah beda dengan
yang di-seed. Tag dipakai karena AdminSite.login tidak menyediakan cara
menyuntik context tambahan tanpa mengganti view-nya.
"""

from django import template

register = template.Library()


@register.simple_tag
def demo_login_accounts(company_code='A'):
    from core.management.commands.seed_demo_users import (
        DEFAULT_PASSWORD, DEMO_ROLES, USER_PROFILE,
    )
    from core.models import User

    prefix = f'demo_{company_code.lower()}_'
    existing = set(
        User.objects.filter(username__startswith=prefix)
        .values_list('username', flat=True)
    )
    if not existing:
        return {'accounts': [], 'password': DEFAULT_PASSWORD}

    labels = dict(User.Role.choices)
    accounts = []
    for role in DEMO_ROLES:
        slug, _fullname = USER_PROFILE[role]
        username = f'{prefix}{slug}'
        if username in existing:
            accounts.append({'role': labels.get(role, role), 'username': username})
    return {'accounts': accounts, 'password': DEFAULT_PASSWORD}
