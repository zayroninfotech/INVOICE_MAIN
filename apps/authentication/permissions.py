from rest_framework.permissions import BasePermission


def _role(user):
    """Safely get role — returns empty string for AnonymousUser."""
    return getattr(user, 'role', '')


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return _role(request.user) == 'superadmin'


class IsAdminOrSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return _role(request.user) in ('user', 'admin', 'superadmin')


class IsReadOnlyForUser(BasePermission):
    """All authenticated users (user/admin/superadmin) have full access."""
    def has_permission(self, request, view):
        return bool(_role(request.user))  # any logged-in role passes
