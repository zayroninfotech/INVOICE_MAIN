from rest_framework import serializers
from .models import Invoice, InvoiceItem
from apps.customers.models import Customer
from apps.products.models import Product
from utils.invoice_number import generate_invoice_number
from datetime import datetime


class InvoiceItemInputSerializer(serializers.Serializer):
    product_id   = serializers.CharField(required=False, allow_blank=True, default='manual')
    quantity     = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    discount     = serializers.DecimalField(max_digits=5, decimal_places=2, default=0)
    description  = serializers.CharField(default='', allow_blank=True, required=False)
    unit_price   = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    tax_rate     = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=0)
    hsn_code     = serializers.CharField(required=False, allow_blank=True, default='')


class InvoiceSerializer(serializers.Serializer):
    id             = serializers.CharField(source='pk', read_only=True)
    invoice_number = serializers.CharField(read_only=True)
    # customer_id is optional — pass empty/omit to use manual fields below
    customer_id      = serializers.CharField(required=False, allow_blank=True, default='manual')
    customer_name    = serializers.CharField(required=False, allow_blank=True, default='')
    customer_email   = serializers.EmailField(required=False, allow_blank=True, default='noemail@example.com')
    customer_address = serializers.CharField(required=False, allow_blank=True, default='')
    customer_gst     = serializers.CharField(required=False, allow_blank=True, default='')
    invoice_date   = serializers.DateTimeField()
    due_date       = serializers.DateTimeField(required=False, allow_null=True)
    items          = InvoiceItemInputSerializer(many=True, write_only=True)
    notes          = serializers.CharField(default='', allow_blank=True)
    terms          = serializers.CharField(default='Payment due within 30 days.', allow_blank=True)
    currency       = serializers.CharField(default='INR')
    template_color = serializers.CharField(default='#F97316', allow_blank=True, required=False)
    status         = serializers.CharField(read_only=True)
    subtotal       = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    tax_amount     = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    grand_total    = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    created_at     = serializers.DateTimeField(read_only=True)

    def _resolve_customer(self, validated_data, user):
        """Return (customer_id, name, email, address, gst) from real customer or manual fields."""
        cid = validated_data.get('customer_id', 'manual') or 'manual'
        if cid and cid != 'manual':
            is_superadmin = getattr(user, 'role', '') == 'superadmin'
            if is_superadmin:
                c = Customer.objects(pk=cid, is_active=True).first()
            else:
                c = Customer.objects(pk=cid, created_by=str(user.pk), is_active=True).first()
            if c:
                addr = ', '.join(filter(None, [c.address, c.city, f"{c.state} {c.pincode}".strip()]))
                return str(c.pk), c.customer_name, c.email, addr, c.gst_number
        # manual fallback
        name  = validated_data.get('customer_name', '') or 'Unknown'
        email = validated_data.get('customer_email', '') or 'noemail@example.com'
        addr  = validated_data.get('customer_address', '') or ''
        gst   = validated_data.get('customer_gst', '') or ''
        return 'manual', name, email, addr, gst

    def _build_items(self, items_data, user_id, is_superadmin=False):
        built = []
        for item_data in items_data:
            pid = (item_data.get('product_id') or '').strip()
            manual_price = item_data.get('unit_price')

            # Try resolving from product catalogue first
            product = None
            if pid and pid != 'manual':
                try:
                    if is_superadmin:
                        product = Product.objects(pk=pid, is_active=True).first()
                    else:
                        product = Product.objects(pk=pid, created_by=user_id, is_active=True).first()
                except Exception:
                    pass

            if product:
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
            else:
                # Manual item — unit_price is required
                price = float(manual_price or 0)
                desc  = item_data.get('description', '') or 'Item'
                built.append(InvoiceItem(
                    product_id='manual',
                    product_name=desc,
                    description=desc,
                    hsn_code=item_data.get('hsn_code', ''),
                    unit='Nos',
                    unit_price=price,
                    quantity=float(item_data['quantity']),
                    tax_rate=float(item_data.get('tax_rate', 0)),
                    discount=float(item_data.get('discount', 0)),
                ))
        return built

    def create(self, validated_data):
        user = self.context['request'].user
        user_id = str(user.pk)
        is_superadmin = getattr(user, 'role', '') == 'superadmin'
        items_data = validated_data.pop('items')

        cid, cname, cemail, caddr, cgst = self._resolve_customer(validated_data, user)

        count = Invoice.objects(created_by=user_id).count()
        invoice_number = generate_invoice_number(count)

        due = validated_data.get('due_date') or validated_data['invoice_date']

        invoice = Invoice(
            invoice_number=invoice_number,
            customer_id=cid,
            customer_name=cname,
            customer_email=cemail,
            customer_address=caddr,
            customer_gst=cgst,
            invoice_date=validated_data['invoice_date'],
            due_date=due,
            notes=validated_data.get('notes', ''),
            terms=validated_data.get('terms', 'Payment due within 30 days.'),
            currency=validated_data.get('currency', 'INR'),
            template_color=validated_data.get('template_color', '#F97316'),
            created_by=user_id,
        )
        invoice.items = self._build_items(items_data, user_id, is_superadmin)
        invoice.calculate_totals()
        return invoice.save()

    def update(self, instance, validated_data):
        user = self.context['request'].user
        user_id = str(user.pk)
        is_superadmin = getattr(user, 'role', '') == 'superadmin'
        items_data = validated_data.pop('items', None)

        cid, cname, cemail, caddr, cgst = self._resolve_customer(validated_data, user)
        instance.customer_id      = cid
        instance.customer_name    = cname
        instance.customer_email   = cemail
        instance.customer_address = caddr
        instance.customer_gst     = cgst

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
