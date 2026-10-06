from django.db.models.signals import post_delete, post_save

from .audit import delete_receiver, receiver

# (app_label, model_name)
AUDITED = [
    ('core', 'Company'),
    ('core', 'Branch'),
    ('core', 'Department'),
    ('core', 'CostCenter'),
    ('gl', 'Account'),
    ('gl', 'AccountingPeriod'),
    ('gl', 'JournalEntry'),
    ('ar', 'Customer'),
    ('ar', 'CustomerInvoice'),
    ('ar', 'CustomerPayment'),
    ('ap', 'Supplier'),
    ('ap', 'SupplierBill'),
    ('ap', 'SupplierPayment'),
    ('bank', 'BankAccount'),
    ('bank', 'BankTransaction'),
]


def bind():
    from django.apps import apps

    for app_label, model_name in AUDITED:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        post_save.connect(receiver, sender=model, dispatch_uid=f'audit_save_{model_name}')
        post_delete.connect(delete_receiver, sender=model, dispatch_uid=f'audit_delete_{model_name}')