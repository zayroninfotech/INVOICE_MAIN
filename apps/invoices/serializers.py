from rest_framework import serializers
from .models import Invoice, InvoiceItem
from apps.customers.models import Customer
from apps.products.models import Product
from utils.invoice_number import generate_invoice_number
from datetime import datetime


class InvoiceItemInputSerializer(serializers.Serializer):
    product_id = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    discount = serializers.DecimalField(max_digits=5, decimal_places=2, default=0)
    description = serializers.CharField(default='', allow_blank=True, required=False)


class InvoiceSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk', read_only=True)
    invoice_number = serializers.CharField(read_only=True)
    customer_id = serializers.CharField()
    invoice_date = serializers.DateTimeField()
    due_date = serializers.DateTimeField()
    items = InvoiceItemInputSerializer(many=True, write_only=True)
    notes = serializers.CharField(default='', allow_blank=True)
    terms = serializers.CharField(default='Payment due within 30 days.', allow_blank=True)
    currency = serializers.CharField(default='INR')
    template_color = serializers.CharField(default='#F97316', allow_blank=True, required=False)
    status = serializers.CharField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    customer_name = serializers.CharField(read_only=True)
    customer_email = serializers.EmailField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def validate_customer_id(self, value):
        user = self.context['request'].user
        if getattr(user, 'role', '') == 'superadmin':
            qs = Customer.objects(pk=value, is_active=True)
        else:
            qs = Customer.objects(pk=value, created_by=str(user.pk), is_active=True)
        if not qs.first():
            raise serializers.ValidationError("Customer not found.")
        return value

    def _build_items(self, items_data, user_id, is_superadmin=False):
        built = []
        for item_data in items_data:
            if is_superadmin:
                product = Product.objects(pk=item_data['product_id'], is_active=True).first()
            else:
                product = Product.objects(pk=item_data['product_id'], created_by=user_id, is_active=True).first()
            if not product:
                raise serializers.ValidationError(f"Product {item_data['product_id']} not found.")
            built.append(InvoiceItem(
                product_id=str(product.pk),
                product_name=product.name,
                description=item_data.get('description', '') or product.description,
                hsn_code=product.hsn_code,
                unit=product.unit,
                unit_price=float(product.price),
                quantity=float(item_data['quantity']),
                tax_rate=float(product.tax_rate),
                discount=float(item_data.get('discount', 0)),
            ))
        return built

    def create(self, validated_data):
        user_id = str(self.context['request'].user.pk)
        items_data = validated_data.pop('items')
        customer = Customer.objects(pk=validated_data['customer_id']).first()

        count = Invoice.objects(created_by=user_id).count()
        invoice_number = generate_invoice_number(count)

        invoice = Invoice(
            invoice_number=invoice_number,
            customer_id=validated_data['customer_id'],
            customer_name=customer.customer_name,
            customer_email=customer.email,
            customer_address=', '.join(filter(None, [customer.address, customer.city, f"{customer.state} {customer.pincode}".strip()])),
            customer_gst=customer.gst_number,
            invoice_date=validated_data['invoice_date'],
            due_date=validated_data['due_date'],
            notes=validated_data.get('notes', ''),
            terms=validated_data.get('terms', 'Payment due within 30 days.'),
            currency=validated_data.get('currency', 'INR'),
            template_color=validated_data.get('template_color', '#F97316'),
            created_by=user_id,
        )
        is_superadmin = getattr(self.context['request'].user, 'role', '') == 'superadmin'
        invoice.items = self._build_items(items_data, user_id, is_superadmin)
        invoice.calculate_totals()
        return invoice.save()

    def update(self, instance, validated_data):
        user_id = str(self.context['request'].user.pk)
        is_superadmin = getattr(self.context['request'].user, 'role', '') == 'superadmin'
        items_data = validated_data.pop('items', None)
        if 'customer_id' in validated_data:
            customer = Customer.objects(pk=validated_data['customer_id']).first()
            instance.customer_id = validated_data['customer_id']
            instance.customer_name = customer.customer_name
            instance.customer_email = customer.email
        for field in ['invoice_date', 'due_date', 'notes', 'terms', 'currency', 'template_color']:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        if items_data is not None:
            instance.items = self._build_items(items_data, user_id, is_superadmin)
        instance.calculate_totals()
        return instance.save()


class InvoiceListSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk')
    invoice_number = serializers.CharField()
    customer_name = serializers.CharField()
    invoice_date = serializers.DateTimeField()
    due_date = serializers.DateTimeField()
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.CharField()
    currency = serializers.CharField()


class InvoiceDetailSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk')
    invoice_number = serializers.CharField()
    customer_id = serializers.CharField()
    customer_name = serializers.CharField()
    customer_email = serializers.EmailField()
    customer_address = serializers.CharField()
    customer_gst = serializers.CharField()
    invoice_date = serializers.DateTimeField()
    due_date = serializers.DateTimeField()
    items = serializers.SerializerMethodField()
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.CharField()
    notes = serializers.CharField()
    terms = serializers.CharField()
    currency = serializers.CharField()
    template_color = serializers.CharField()
    created_at = serializers.DateTimeField()

    def get_items(self, obj):
        return [{
            'product_id': item.product_id,
            'product_name': item.product_name,
            'description': item.description,
            'hsn_code': item.hsn_code,
            'unit': item.unit,
            'unit_price': float(item.unit_price),
            'quantity': float(item.quantity),
            'tax_rate': float(item.tax_rate),
            'discount': float(item.discount),
            'subtotal': float(item.subtotal),
            'tax_amount': float(item.tax_amount),
            'total': float(item.total),
        } for item in obj.items]
