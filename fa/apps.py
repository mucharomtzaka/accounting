from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fa'
    verbose_name = _('Fixed Assets')
