from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from main.models import IdempotencyRecord


class Command(BaseCommand):
    help = "Report idempotency keys stuck in 'processing' state."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=10, help="Consider records older than N minutes stuck.")

    def handle(self, *args, **options):
        minutes = int(options["minutes"])
        cutoff = timezone.now() - timedelta(minutes=minutes)
        qs = IdempotencyRecord.objects.filter(is_deleted=False, state="processing", created_at__lt=cutoff).order_by("created_at")
        count = qs.count()
        self.stdout.write(self.style.WARNING(f"Found {count} stuck idempotency record(s) (> {minutes} minutes)."))
        for r in qs[:200]:
            self.stdout.write(f"- {r.created_at.isoformat()} key={r.key} scope={r.scope} path={r.path} request_hash={r.request_hash}")

