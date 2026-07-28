from rest_framework import serializers
from django.contrib.auth.hashers import make_password, check_password
from .models import User


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)

    def validate_username(self, value):
        if User.objects(username=value).first():
            raise serializers.ValidationError("Username already exists.")
        return value

    def validate_email(self, value):
        if User.objects(email=value).first():
            raise serializers.ValidationError("Email already registered.")
        return value

    def create(self, validated_data):
        validated_data['password'] = make_password(validated_data['password'])
        validated_data['role'] = 'user'  # public registration always creates user role
        return User(**validated_data).save()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = User.objects(email=data['email'], is_active=True).first()
        if not user or not check_password(data['password'], user.password):
            raise serializers.ValidationError("Invalid email or password.")
        data['user'] = user
        return data


class UserSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk')
    username = serializers.CharField()
    email = serializers.EmailField()
    role = serializers.CharField()
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(min_length=8, write_only=True)


class CreateUserSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    role = serializers.ChoiceField(choices=['superadmin', 'admin', 'user'], default='user')

    def validate_username(self, value):
        if User.objects(username=value).first():
            raise serializers.ValidationError("Username already exists.")
        return value

    def validate_email(self, value):
        if User.objects(email=value).first():
            raise serializers.ValidationError("Email already registered.")
        return value

    def create(self, validated_data):
        validated_data['password'] = make_password(validated_data['password'])
        return User(**validated_data).save()
