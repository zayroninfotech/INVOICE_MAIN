from django.conf import settings
from django.core.management.base import BaseCommand
from pymongo import MongoClient


def _host(uri):
    return uri.split('://', 1)[-1].rsplit('@', 1)[-1].split('/')[0] if uri else '(not set)'


class Command(BaseCommand):
    help = "Check the MongoDB connection and list collections with document counts."

    def handle(self, *args, **opts):
        db_name = settings.MONGODB_DB
        for label, uri in (('PRIMARY', settings.MONGODB_URI_PRIMARY),
                           ('FALLBACK', settings.MONGODB_URI_FALLBACK)):
            if not uri:
                self.stdout.write(self.style.WARNING(f"{label}: not set in .env"))
                continue
            try:
                client = MongoClient(uri, serverSelectionTimeoutMS=4000)
                info = client.server_info()
                db = client[db_name]
                names = sorted(db.list_collection_names())
                self.stdout.write(self.style.SUCCESS(
                    f"{label}: OK  {_host(uri)}  MongoDB {info.get('version')}  db={db_name}  "
                    f"{len(names)} collections"))
                for n in names:
                    self.stdout.write(f"    {n:<28} {db[n].estimated_document_count():>8}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"{label}: FAILED  {_host(uri)}  {type(exc).__name__}: {exc}"))

        using = 'PRIMARY' if settings.MONGO_URI == settings.MONGODB_URI_PRIMARY else 'FALLBACK'
        self.stdout.write(f"\nThe app is currently using: {using} ({_host(settings.MONGO_URI)})")
