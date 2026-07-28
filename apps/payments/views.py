from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.authentication.authentication import MongoJWTAuthentication
from .models import Payment
from .serializers import PaymentSerializer
from utils.response import success, error


class PaymentListCreateView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        invoice_id = request.query_params.get('invoice_id', '')
        user = request.user
        queryset = Payment.objects() if getattr(user, 'role', '') == 'superadmin' else Payment.objects(created_by=str(user.pk))
        if invoice_id:
            queryset = queryset.filter(invoice_id=invoice_id)
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        offset = (page - 1) * page_size
        total = queryset.count()
        payments = queryset.skip(offset).limit(page_size)
        return success({
            'total': total,
            'page': page,
            'page_size': page_size,
            'results': PaymentSerializer(payments, many=True).data,
        })

    def post(self, request):
        serializer = PaymentSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        payment = serializer.save()
        return success(PaymentSerializer(payment).data, "Payment recorded.", 201)


class PaymentDetailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        user = request.user
        if getattr(user, 'role', '') == 'superadmin':
            payment = Payment.objects(pk=pk).first()
        else:
            payment = Payment.objects(pk=pk, created_by=str(user.pk)).first()
        if not payment:
            return error("Payment not found.", status=404)
        return success(PaymentSerializer(payment).data)
