from datetime import datetime, timedelta
from django.conf import settings
import jwt


def generate_tokens(user):
    now = datetime.utcnow()
    access_payload = {
        'user_id': str(user.pk),
        'email': user.email,
        'role': user.role,
        'exp': now + timedelta(minutes=settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].seconds // 60),
        'iat': now,
        'type': 'access',
    }
    refresh_payload = {
        'user_id': str(user.pk),
        'exp': now + settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'],
        'iat': now,
        'type': 'refresh',
    }
    access_token = jwt.encode(access_payload, settings.SECRET_KEY, algorithm='HS256')
    refresh_token = jwt.encode(refresh_payload, settings.SECRET_KEY, algorithm='HS256')
    return {'access': access_token, 'refresh': refresh_token}


def decode_token(token):
    return jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
