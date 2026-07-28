from rest_framework.views import APIView
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsReadOnlyForUser
from .models import Product
from .serializers import ProductSerializer
from utils.response import success, error


def _qs_for_user(user):
    if getattr(user, 'role', '') == 'superadmin':
        return Product.objects(is_active=True)
    return Product.objects(created_by=str(user.pk), is_active=True)


class ProductListCreateView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        search = request.query_params.get('search', '')
        category = request.query_params.get('category', '')
        queryset = _qs_for_user(request.user)
        if search:
            queryset = queryset.filter(__raw__={'name': {'$regex': search, '$options': 'i'}})
        if category:
            queryset = queryset.filter(category=category)
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        offset = (page - 1) * page_size
        total = queryset.count()
        products = queryset.skip(offset).limit(page_size)
        return success({'total': total, 'page': page, 'page_size': page_size,
                        'results': ProductSerializer(products, many=True).data})

    def post(self, request):
        serializer = ProductSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        product = serializer.save()
        return success(ProductSerializer(product).data, "Product created.", 201)


class ProductDetailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def _get(self, pk, user):
        if user.role == 'superadmin':
            return Product.objects(pk=pk, is_active=True).first()
        return Product.objects(pk=pk, created_by=str(user.pk), is_active=True).first()

    def get(self, request, pk):
        product = self._get(pk, request.user)
        if not product:
            return error("Product not found.", status=404)
        return success(ProductSerializer(product).data)

    def put(self, request, pk):
        product = self._get(pk, request.user)
        if not product:
            return error("Product not found.", status=404)
        serializer = ProductSerializer(product, data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        product = serializer.update(product, serializer.validated_data)
        return success(ProductSerializer(product).data, "Product updated.")

    def delete(self, request, pk):
        product = self._get(pk, request.user)
        if not product:
            return error("Product not found.", status=404)
        product.is_active = False
        product.save()
        return success(message="Product deleted.")


class ProductCategoriesView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        categories = _qs_for_user(request.user).distinct('category')
        return success({'categories': list(categories)})
