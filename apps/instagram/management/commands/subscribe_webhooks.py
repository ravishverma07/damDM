"""Subscribe connected Instagram accounts to webhooks. Safe to re-run."""

from django.core.management.base import BaseCommand

from apps.instagram.models import ConnectedAccount
from apps.instagram.services.subscriptions import subscribe, subscribed_fields


class Command(BaseCommand):
    help = "Ask Instagram to send webhooks for every active connected account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Include accounts already subscribed."
        )

    def handle(self, *args, **options):
        accounts = ConnectedAccount.objects.filter(status=ConnectedAccount.Status.ACTIVE)
        if not options["all"]:
            accounts = accounts.filter(webhooks_subscribed=False)
        if not accounts:
            self.stdout.write("Nothing to do.")
            return
        for account in accounts:
            ok = subscribe(account)
            style = self.style.SUCCESS if ok else self.style.WARNING
            self.stdout.write(style(f"@{account.username}: {'subscribed' if ok else 'failed'}"))
            if ok:
                self.stdout.write(f"  fields: {', '.join(subscribed_fields(account)) or 'none'}")
