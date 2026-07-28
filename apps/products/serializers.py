from rest_framework import serializers
from .models import Product


class ProductSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk', read_only=True)
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(default='', allow_blank=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, default=18.0)
    unit = serializers.CharField(max_length=50, default='piece')
    category = serializers.CharField(max_length=100, default='General')
    hsn_code = serializers.CharField(max_length=20, default='', allow_blank=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def create(self, validated_data):
        validated_data['created_by'] = str(self.context['request'].user.pk)
        return Product(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        return instance.save()
