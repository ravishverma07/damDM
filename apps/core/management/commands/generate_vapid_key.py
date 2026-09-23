"""Create the key that signs push notifications."""

from django.core.management.base import BaseCommand

from apps.core import push


class Command(BaseCommand):
    help = "Print a new VAPID_PRIVATE_KEY for Web Push notifications."

    def handle(self, *args, **options):
        self.stdout.write(f"VAPID_PRIVATE_KEY={push.generate_private_key()}")
        self.stdout.write(self.style.WARNING(
            "\nCopy this into .env and keep it secret. Changing it later means every device "
            "has to turn notifications on again."
        ))
