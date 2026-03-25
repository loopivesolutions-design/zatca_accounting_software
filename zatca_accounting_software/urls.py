"""
URL configuration for zatca_accounting_software project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/main/", include("main.urls")),
    path("api/v1/user/", include("user.urls", namespace="user")),
    path("api/v1/accounting/", include("accounting.urls", namespace="accounting")),
    path("api/v1/products/", include("products.urls", namespace="products")),
    path("api/v1/purchases/", include("purchases.urls", namespace="purchases")),
    path("api/v1/sales/", include("sales.urls", namespace="sales")),
]

# Serve media files (product images, etc.) during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
