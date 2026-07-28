from rest_framework import serializers
from .models import Customer


class CustomerSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk', read_only=True)
    customer_name = serializers.CharField(max_length=200)
    company = serializers.CharField(max_length=200, default='', allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=20, default='', allow_blank=True)
    address = serializers.CharField(default='', allow_blank=True)
    city = serializers.CharField(max_length=100, default='', allow_blank=True)
    state = serializers.CharField(max_length=100, default='', allow_blank=True)
    pincode = serializers.CharField(max_length=10, default='', allow_blank=True)
    gst_number = serializers.CharField(max_length=20, default='', allow_blank=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def create(self, validated_data):
        validated_data['created_by'] = str(self.context['request'].user.pk)
        return Customer(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        return instance.save()


class CustomerListSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk')
    customer_name = serializers.CharField()
    company = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField()
    gst_number = serializers.CharField()
    created_at = serializers.DateTimeField()
