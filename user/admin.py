from django.contrib import admin
from .models import CustomUser, Role, RolePermission, UserInvitation

admin.site.register(CustomUser)
admin.site.register(Role)
admin.site.register(RolePermission)
admin.site.register(UserInvitation)
