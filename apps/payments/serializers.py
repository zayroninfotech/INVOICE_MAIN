from rest_framework import serializers
from .models import Payment
from apps.invoices.models import Invoice


class PaymentSerializer(serializers.Serializer):
    id = serializers.CharField(source='pk', read_only=True)
    invoice_id = serializers.CharField()
    invoice_number = serializers.CharField(read_only=True)
    customer_name = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    payment_date = serializers.DateTimeField()
    payment_method = serializers.ChoiceField(
        choices=['Cash', 'Bank Transfer', 'UPI', 'Cheque', 'Card', 'Other'],
        default='Bank Transfer'
    )
    reference_number = serializers.CharField(default='', allow_blank=True)
    notes = serializers.CharField(default='', allow_blank=True)
    created_at = serializers.DateTimeField(read_only=True)

    def validate_invoice_id(self, value):
        user = self.context['request'].user
        if getattr(user, 'role', '') == 'superadmin':
            invoice = Invoice.objects(pk=value).first()
        else:
            invoice = Invoice.objects(pk=value, created_by=str(user.pk)).first()
        if not invoice:
            raise serializers.ValidationError("Invoice not found.")
        if invoice.status == 'Cancelled':
            raise serializers.ValidationError("Cannot record payment for a cancelled invoice.")
        self.context['invoice'] = invoice
        return value

    def create(self, validated_data):
        user_id = str(self.context['request'].user.pk)
        invoice = self.context['invoice']
        payment = Payment(
            invoice_id=validated_data['invoice_id'],
            invoice_number=invoice.invoice_number,
            customer_name=invoice.customer_name,
            amount=validated_data['amount'],
            payment_date=validated_data['payment_date'],
            payment_method=validated_data.get('payment_method', 'Bank Transfer'),
            reference_number=validated_data.get('reference_number', ''),
            notes=validated_data.get('notes', ''),
            created_by=user_id,
        ).save()

        # Update invoice status
        total_paid = sum(
            float(p.amount) for p in Payment.objects(invoice_id=validated_data['invoice_id'])
        )
        grand_total = float(invoice.grand_total)
        if total_paid >= grand_total:
            invoice.status = 'Paid'
        elif total_paid > 0:
            invoice.status = 'Partial'
        invoice.save()
        return payment
