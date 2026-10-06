from .models import AuditLog
from .threadlocal import get_request

SKIP_FIELDS = {'updated_at', 'updated_by', 'created_at', 'created_by', 'id', 'company'}


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in SKIP_FIELDS:
            continue
        value = getattr(instance, field.attname, None)
        data[field.name] = '' if value is None else str(value)
    return data


def log(instance, action, previous=None):
    request = get_request()
    user = getattr(request, 'user', None)
    if user is not None and not user.is_authenticated:
        user = None

    current = _snapshot(instance)
    if action == 'UPDATE' and previous:
        changes = {k: (previous.get(k, ''), v) for k, v in current.items() if previous.get(k) != v}
    else:
        changes = {k: ('', v) for k, v in current.items()}

    AuditLog.objects.bulk_create([
        AuditLog(
            company=getattr(instance, 'company_id', None) and instance.company,
            user=user,
            model_name=instance._meta.object_name,
            object_id=str(instance.pk),
            action=action,
            field_name=field,
            previous_value=str(old),
            new_value=str(new),
        )
        for field, (old, new) in changes.items()
    ])


def receiver(sender, instance, created, **kwargs):
    if created:
        log(instance, 'CREATE')
        return
    previous = _snapshot_from_db(sender, instance)
    if previous:
        log(instance, 'UPDATE', previous)


def _snapshot_from_db(model, instance):
    if not instance.pk:
        return None
    row = model.objects.filter(pk=instance.pk).values().first()
    if not row:
        return None
    return {k: ('' if v is None else str(v)) for k, v in row.items()}


def delete_receiver(sender, instance, **kwargs):
    log(instance, 'DELETE')