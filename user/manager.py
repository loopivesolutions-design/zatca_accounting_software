from django.contrib.auth.base_user import BaseUserManager
from django.utils.translation import gettext_lazy as _


class CustomUserManager(BaseUserManager):
    def create_user(
        self,
        phone=None,
        email=None,
        first_name=None,
        last_name=None,
        password=None,
        **extra_fields
    ):
        """
        Create and return a normal user with phone (for normal users) and optional email
        """
        if not phone and not email:
            raise ValueError(_("The Phone or Email field must be set"))
        # Normalize email if provided
        if email:
            email = self.normalize_email(email)

        # Create the user object with phone (and email if provided)
        user = self.model(
            phone=phone,
            email=email,
            first_name=first_name,
            last_name=last_name,
            **extra_fields
        )
        # If a password is provided, set it
        if password:
            user.set_password(password)

        # Save the user object to the database
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email, first_name=None, last_name=None, password=None, **extra_fields
    ):
        """
        Create and return a superuser with email and password
        """
        if not email:
            raise ValueError(_("Superuser must have an email address"))

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True."))

        # Superuser should use email
        user = self.create_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            **extra_fields
        )
        return user

    def create_admin(
        self, email, first_name=None, last_name=None, password=None, **extra_fields
    ):
        """
        Create and return an admin user with email and password (not a superuser)
        """
        if not email:
            raise ValueError(_("Admin user must have an email address"))

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", False)  # Admin is not a superuser
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Admin user must have is_staff=True."))

        # Admin user should use email
        user = self.create_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            **extra_fields
        )
        return user
