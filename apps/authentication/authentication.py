from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .jwt_utils import decode_token
from .models import User
import jwt


class MongoJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None
        token = auth_header.split(' ')[1]
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Token has expired.")
        except jwt.InvalidTokenError:
            raise AuthenticationFailed("Invalid token.")

        if payload.get('type') != 'access':
            raise AuthenticationFailed("Invalid token type.")

        user = User.objects(pk=payload['user_id'], is_active=True).first()
        if not user:
            raise AuthenticationFailed("User not found.")
        return (user, token)

    def www_authenticate(self):
        return 'Bearer realm="api"'
