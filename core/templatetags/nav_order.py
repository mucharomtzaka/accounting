"""Filter untuk template admin: urutkan app_list dari jumlah model terkecil."""
from django import template

register = template.Library()


@register.filter
def by_model_count(app_list):
    """Urutkan app dari grup paling kecil ke terbesar.

    Key berawalan nol supaya pengurutan string tetap urut numerik
    (2 model sebelum 10 model).
    """
    return sorted(app_list, key=lambda app: f'{len(app["models"]):03d}{app["name"]}')