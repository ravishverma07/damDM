"""Generate the secrets a fresh DamDM install needs."""

from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand
from django.core.management.utils import get_random_secret_key


class Command(BaseCommand):
    help = "Print freshly generated SECRET_KEY, TOKEN_ENCRYPTION_KEY and IP_HASH_SALT values."

    def handle(self, *args, **options):
        self.stdout.write(f"SECRET_KEY={get_random_secret_key()}")
        self.stdout.write(f"TOKEN_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
        self.stdout.write(f"IP_HASH_SALT={get_random_secret_key()[:32]}")
        self.stdout.write(
            self.style.WARNING("\nCopy these into .env - never commit them.")
        )
