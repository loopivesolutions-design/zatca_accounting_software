from django.core.management.base import BaseCommand

from main.idempotency_lease import DEFAULT_STALE_PROCESSING_SECONDS, reclaim_stale_processing_records


class Command(BaseCommand):
    help = (
        "Mark IdempotencyRecord rows stuck in processing (e.g. worker crash) as failed so "
        "clients can retry. Run from cron alongside ZATCA/outbox workers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=float,
            default=DEFAULT_STALE_PROCESSING_SECONDS / 60,
            help=f"Age threshold in minutes (default: {DEFAULT_STALE_PROCESSING_SECONDS / 60}).",
        )

    def handle(self, *args, **options):
        minutes = float(options["minutes"])
        seconds = max(1, int(minutes * 60))
        n = reclaim_stale_processing_records(max_age_seconds=seconds)
        self.stdout.write(self.style.SUCCESS(f"Reclaimed {n} stale idempotency record(s) (>{seconds}s in processing)."))
