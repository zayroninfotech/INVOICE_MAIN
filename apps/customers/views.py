from rest_framework.views import APIView
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsReadOnlyForUser
from .models import Customer
from .serializers import CustomerSerializer, CustomerListSerializer
from utils.response import success, error


def _qs_for_user(user):
    if getattr(user, 'role', '') == 'superadmin':
        return Customer.objects(is_active=True)
    return Customer.objects(created_by=str(user.pk), is_active=True)


class CustomerListCreateView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        search = request.query_params.get('search', '')
        queryset = _qs_for_user(request.user)
        if search:
            queryset = queryset.filter(
                __raw__={'$or': [
                    {'customer_name': {'$regex': search, '$options': 'i'}},
                    {'email': {'$regex': search, '$options': 'i'}},
                    {'company': {'$regex': search, '$options': 'i'}},
                ]}
            )
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        offset = (page - 1) * page_size
        total = queryset.count()
        customers = queryset.skip(offset).limit(page_size)
        return success({
            'total': total,
            'page': page,
            'page_size': page_size,
            'results': CustomerListSerializer(customers, many=True).data,
        })

    def post(self, request):
        serializer = CustomerSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        customer = serializer.save()
        return success(CustomerSerializer(customer).data, "Customer created.", 201)


class CustomerDetailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def _get_customer(self, pk, user):
        if user.role == 'superadmin':
            return Customer.objects(pk=pk, is_active=True).first()
        return Customer.objects(pk=pk, created_by=str(user.pk), is_active=True).first()

    def get(self, request, pk):
        customer = self._get_customer(pk, request.user)
        if not customer:
            return error("Customer not found.", status=404)
        return success(CustomerSerializer(customer).data)

    def put(self, request, pk):
        customer = self._get_customer(pk, request.user)
        if not customer:
            return error("Customer not found.", status=404)
        serializer = CustomerSerializer(customer, data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        customer = serializer.update(customer, serializer.validated_data)
        return success(CustomerSerializer(customer).data, "Customer updated.")

    def delete(self, request, pk):
        customer = self._get_customer(pk, request.user)
        if not customer:
            return error("Customer not found.", status=404)
        customer.is_active = False
        customer.save()
        return success(message="Customer deleted.")
