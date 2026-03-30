from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from main.models import IdempotencyRecord


class Command(BaseCommand):
    help = "Cleanup old idempotency records (retention policy)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Delete succeeded/failed records older than N days.")

    def handle(self, *args, **options):
        days = int(options["days"])
        cutoff = timezone.now() - timedelta(days=days)

        qs = IdempotencyRecord.objects.filter(is_deleted=False, created_at__lt=cutoff).filter(state__in=["succeeded", "failed"])
        count = qs.count()
        qs.update(is_deleted=True, updated_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"Soft-deleted {count} idempotency record(s) older than {days} days."))

