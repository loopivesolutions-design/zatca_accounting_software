from django.core.management.base import BaseCommand
from django.utils import timezone

from sales.models import ZatcaCertificate


class Command(BaseCommand):
    help = "Activate a ZATCA certificate record and mark it as active."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Logical certificate name.")
        parser.add_argument("--private-key-path", required=True, help="Filesystem path to private key PEM.")
        parser.add_argument("--certificate-pem", required=False, default="", help="Certificate PEM content (optional).")

    def handle(self, *args, **options):
        name = options["name"].strip()
        private_key_path = options["private_key_path"].strip()
        certificate_pem = options["certificate_pem"] or ""

        ZatcaCertificate.objects.filter(is_deleted=False, is_active=True).update(is_active=False, updated_at=timezone.now())

        cert, _ = ZatcaCertificate.objects.get_or_create(name=name, defaults={})
        cert.private_key_path = private_key_path
        cert.certificate_pem = certificate_pem
        cert.is_active = True
        cert.activated_at = timezone.now()
        cert.save()

        self.stdout.write(self.style.SUCCESS(f"Active certificate set: {cert.id} ({cert.name})"))

