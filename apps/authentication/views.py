from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth.hashers import make_password, check_password
from django.conf import settings
from .serializers import RegisterSerializer, LoginSerializer, UserSerializer, ChangePasswordSerializer, CreateUserSerializer
from .jwt_utils import generate_tokens, decode_token
from .authentication import MongoJWTAuthentication
from .permissions import IsSuperAdmin
from .models import User, BusinessProfile
from utils.response import success, error
import jwt
import os
import uuid
import secrets
import string


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        user = serializer.save()
        # Auto-create free subscription
        try:
            from apps.subscriptions.models import Subscription
            Subscription(user_id=str(user.pk)).save()
        except Exception:
            pass
        tokens = generate_tokens(user)
        return success({'user': UserSerializer(user).data, 'tokens': tokens}, "Registration successful.", 201)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        # Support login by email OR username
        identifier = request.data.get('email') or request.data.get('username', '')
        password = request.data.get('password', '')
        user = (
            User.objects(email=identifier, is_active=True).first() or
            User.objects(username=identifier, is_active=True).first()
        )
        if not user or not check_password(password, user.password):
            return error("Invalid credentials.", status=401)
        tokens = generate_tokens(user)
        return success({'user': UserSerializer(user).data, 'tokens': tokens}, "Login successful.")


class RefreshTokenView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return error("Refresh token required.", status=400)
        try:
            payload = decode_token(refresh_token)
            if payload.get('type') != 'refresh':
                return error("Invalid token type.", status=400)
            user = User.objects(pk=payload['user_id'], is_active=True).first()
            if not user:
                return error("User not found.", status=404)
            tokens = generate_tokens(user)
            return success({'tokens': tokens}, "Token refreshed.")
        except jwt.ExpiredSignatureError:
            return error("Refresh token expired.", status=401)
        except jwt.InvalidTokenError:
            return error("Invalid refresh token.", status=401)


class MeView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        user = request.user
        if not check_password(serializer.validated_data['old_password'], user.password):
            return error("Old password is incorrect.", status=400)
        user.password = make_password(serializer.validated_data['new_password'])
        user.save()
        return success(message="Password changed successfully.")


# ── Superadmin: User Management ──────────────────────────────────────────────

class UserListView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        serializer = CreateUserSerializer(data=request.data)
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        user = serializer.save()
        return success(UserSerializer(user).data, "User created.", 201)

    def get(self, request):
        search = request.query_params.get('search', '')
        role_filter = request.query_params.get('role', '')
        queryset = User.objects()
        if search:
            queryset = queryset.filter(
                __raw__={'$or': [
                    {'username': {'$regex': search, '$options': 'i'}},
                    {'email': {'$regex': search, '$options': 'i'}},
                ]}
            )
        if role_filter:
            queryset = queryset.filter(role=role_filter)
        page = int(request.query_params.get('page', 1))
        page_size = 20
        total = queryset.count()
        users = queryset.order_by('-created_at').skip((page - 1) * page_size).limit(page_size)
        return success({
            'total': total,
            'page': page,
            'results': UserSerializer(users, many=True).data,
        })


class UserDetailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request, pk):
        user = User.objects(pk=pk).first()
        if not user:
            return error("User not found.", status=404)
        return success(UserSerializer(user).data)

    def patch(self, request, pk):
        user = User.objects(pk=pk).first()
        if not user:
            return error("User not found.", status=404)
        if str(user.pk) == str(request.user.pk):
            return error("Cannot modify your own account here.")
        role = request.data.get('role')
        is_active = request.data.get('is_active')
        if role and role in ('superadmin', 'admin', 'user'):
            user.role = role
        if is_active is not None:
            user.is_active = bool(is_active)
        user.save()
        return success(UserSerializer(user).data, "User updated.")

    def delete(self, request, pk):
        user = User.objects(pk=pk).first()
        if not user:
            return error("User not found.", status=404)
        if str(user.pk) == str(request.user.pk):
            return error("Cannot delete your own account.")
        user.is_active = False
        user.save()
        return success(message="User deactivated.")


class UserStatsView(APIView):
    """Superadmin: per-user activity stats."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request, pk):
        from apps.invoices.models import Invoice
        from apps.customers.models import Customer
        from apps.payments.models import Payment

        user = User.objects(pk=pk).first()
        if not user:
            return error("User not found.", status=404)

        uid = str(user.pk)
        invoices = Invoice.objects(created_by=uid)
        inv_count = invoices.count()
        revenue = sum(float(i.grand_total) for i in invoices)
        paid_count = invoices.filter(status='Paid').count()
        overdue_count = invoices.filter(status='Overdue').count()
        cust_count = Customer.objects(created_by=uid).count()

        # Recent 5 invoices
        recent = invoices.order_by('-created_at').limit(5)
        recent_list = [{
            'invoice_number': i.invoice_number,
            'customer_name': i.customer_name,
            'grand_total': float(i.grand_total),
            'status': i.status,
            'invoice_date': i.invoice_date.strftime('%d %b %Y') if i.invoice_date else '',
        } for i in recent]

        bp = BusinessProfile.objects(user_id=uid).first()
        company = bp.company_name if bp else ''

        return success({
            'user': UserSerializer(user).data,
            'company_name': company,
            'invoice_count': inv_count,
            'revenue': round(revenue, 2),
            'paid_count': paid_count,
            'overdue_count': overdue_count,
            'customer_count': cust_count,
            'recent_invoices': recent_list,
        })


class UserResetPasswordView(APIView):
    """Superadmin: generate and set a new password for a user."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        user = User.objects(pk=pk).first()
        if not user:
            return error("User not found.", status=404)
        if str(user.pk) == str(request.user.pk):
            return error("Cannot reset your own password here.")

        new_password = request.data.get('new_password', '').strip()
        if not new_password:
            # Auto-generate a secure 12-char password
            alphabet = string.ascii_letters + string.digits + '!@#$'
            new_password = ''.join(secrets.choice(alphabet) for _ in range(12))

        if len(new_password) < 6:
            return error("Password must be at least 6 characters.")

        user.password = make_password(new_password)
        user.save()
        return success({'new_password': new_password}, "Password reset successfully.")


# ── Business Profile ─────────────────────────────────────────────────────────

def _bp_data(bp):
    return {
        'company_name': bp.company_name,
        'logo_path':    bp.logo_path,
        'logo_url':     (settings.MEDIA_URL + bp.logo_path) if bp.logo_path else '',
        'address':      bp.address,
        'city':         bp.city,
        'state':        bp.state,
        'pincode':      bp.pincode,
        'phone':        bp.phone,
        'email':        bp.email,
        'gst':          bp.gst,
        'website':      bp.website,
    }


class BusinessProfileView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes     = [IsAuthenticated]

    def _get_or_create(self, user):
        bp = BusinessProfile.objects(user_id=str(user.pk)).first()
        if not bp:
            bp = BusinessProfile(user_id=str(user.pk)).save()
        return bp

    def get(self, request):
        bp = self._get_or_create(request.user)
        return success(_bp_data(bp))

    def put(self, request):
        bp = self._get_or_create(request.user)
        fields = ['company_name', 'address', 'city', 'state', 'pincode',
                  'phone', 'email', 'gst', 'website']
        for f in fields:
            if f in request.data:
                setattr(bp, f, str(request.data[f]).strip())
        bp.save()
        return success(_bp_data(bp), "Business profile updated.")


class BusinessProfileLogoView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes     = [IsAuthenticated]

    ALLOWED_TYPES = {'image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp'}
    MAX_SIZE      = 2 * 1024 * 1024  # 2 MB

    def post(self, request):
        logo = request.FILES.get('logo')
        if not logo:
            return error("No file uploaded.")
        if logo.content_type not in self.ALLOWED_TYPES:
            return error("Only PNG/JPG/GIF/WEBP images are allowed.")
        if logo.size > self.MAX_SIZE:
            return error("File too large. Max 2 MB.")

        bp = BusinessProfile.objects(user_id=str(request.user.pk)).first()
        if not bp:
            bp = BusinessProfile(user_id=str(request.user.pk))

        # Delete old logo if exists
        if bp.logo_path:
            old = os.path.join(settings.MEDIA_ROOT, bp.logo_path)
            if os.path.exists(old):
                os.remove(old)

        ext = logo.name.rsplit('.', 1)[-1].lower()
        filename = f"logo_{request.user.pk}_{uuid.uuid4().hex[:8]}.{ext}"
        logo_dir  = os.path.join(settings.MEDIA_ROOT, 'logos')
        os.makedirs(logo_dir, exist_ok=True)
        filepath  = os.path.join(logo_dir, filename)
        with open(filepath, 'wb') as f:
            for chunk in logo.chunks():
                f.write(chunk)

        bp.logo_path = f"logos/{filename}"
        bp.save()
        return success({
            'logo_path': bp.logo_path,
            'logo_url':  settings.MEDIA_URL + bp.logo_path,
        }, "Logo uploaded.")

    def delete(self, request):
        bp = BusinessProfile.objects(user_id=str(request.user.pk)).first()
        if bp and bp.logo_path:
            old = os.path.join(settings.MEDIA_ROOT, bp.logo_path)
            if os.path.exists(old):
                os.remove(old)
            bp.logo_path = ''
            bp.save()
        return success(message="Logo removed.")
