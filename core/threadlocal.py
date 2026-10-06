from contextvars import ContextVar

_request = ContextVar('current_request', default=None)


def set_request(request):
    _request.set(request)


def reset_request():
    """Lepas request dari context.

    Tanpa ini ContextVar masih berisi request sebelumnya setelah response
    selesai, jadi operasi berikutnya di thread yang sama (management command,
    worker, atau test berikutnya) akan mencatat AuditLog atas nama user yang
    salah - atau gagal karena user itu sudah tidak ada.
    """
    _request.set(None)


def get_request():
    return _request.get()


def get_user():
    request = get_request()
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user
    return None