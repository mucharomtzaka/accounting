from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gl'
    verbose_name = _('General Ledger')
