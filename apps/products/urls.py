from django.urls import path
from .views import ProductListCreateView, ProductDetailView, ProductCategoriesView

urlpatterns = [
    path('', ProductListCreateView.as_view(), name='product_list'),
    path('categories/', ProductCategoriesView.as_view(), name='product_categories'),
    path('<str:pk>/', ProductDetailView.as_view(), name='product_detail'),
]
