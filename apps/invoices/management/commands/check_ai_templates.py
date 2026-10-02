"""Inspect and run AI template designs from the server terminal.

    python manage.py check_ai_templates                 # list designs + status, check setup
    python manage.py check_ai_templates --run c-1a2b3c4d  # design one now, step by step

--run works in the foreground (no web-request time limit) and prints each
step, so a failure shows its real cause instead of a card stuck on
"Designing…".
"""
import os
import time
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "List AI-designed templates and their status, or run one design now (--run <template id>)."

    def add_arguments(self, parser):
        parser.add_argument('--run', default='', help='Template id (c-xxxxxxxx) to design now, in this terminal.')

    def ok(self, m):
        self.stdout.write(self.style.SUCCESS('  OK    ' + m))

    def bad(self, m):
        self.stdout.write(self.style.ERROR('  FAIL  ' + m))

    def handle(self, *args, **o):
        from apps.invoices.models import CustomTemplate
        from apps.invoices import ai_template as ai

        self.stdout.write(self.style.MIGRATE_HEADING('Setup'))
        key = os.environ.get('OPENAI_API_KEY', '').strip()
        (self.ok if key else self.bad)('OPENAI_API_KEY ' + ('is set' if key else 'is NOT set in .env'))
        self.stdout.write(f"        model: {os.environ.get('OPENAI_MODEL') or ai.DEFAULT_MODEL}")
        try:
            import openai
            self.ok(f'openai library {openai.__version__}')
        except ImportError:
            self.bad('openai library not installed (venv/bin/pip install openai)')
        import shutil
        lo = shutil.which('soffice') or shutil.which('libreoffice')
        self.stdout.write(f"  {'OK  ' if lo else 'INFO'}  LibreOffice: {lo or 'not installed — Word files are sent without a page picture'}")

        if not o['run']:
            self.stdout.write(self.style.MIGRATE_HEADING('\nAI templates'))
            rows = CustomTemplate.objects(mode='ai', is_active=True).order_by('-created_at')
            if not rows:
                self.stdout.write('  (none)')
            now = datetime.utcnow()
            for c in rows:
                age = int((now - (c.updated_at or c.created_at)).total_seconds() // 60)
                line = f"  {c.template_id}  {c.ai_status or '-':8}  {c.status:9}  {age:>4} min  {c.name}  [{c.source_name}]"
                if c.ai_status == 'failed':
                    line += f"\n        error: {c.ai_error}"
                if c.ai_status == 'working' and age >= ai.STALE_MINUTES:
                    line += "\n        stuck — the background job stopped. Run it here:  --run " + c.template_id
                self.stdout.write(line)
            return

        tid = o['run'].strip()
        c = CustomTemplate.objects(template_id=tid).first()
        if not c:
            self.bad(f"No template with id {tid}. Run without --run to list the current ids.")
            return
        if not c.is_active:
            self.bad(f"{tid} ({c.name}) was removed on the Add Invoice page. Upload the design again "
                     "to make a new one, then run this with the new id.")
            return
        if c.mode != 'ai':
            self.bad(f"{tid} ({c.name}) isn't an AI design (it's '{c.mode}'), so there is nothing to run.")
            return
        self.stdout.write(self.style.MIGRATE_HEADING(f'\nDesigning {c.template_id} — {c.name}'))
        path = os.path.join(settings.MEDIA_ROOT, c.source_path or '')
        if not c.source_path or not os.path.exists(path):
            self.bad(f'uploaded file is missing: {path}')
            return
        self.ok(f'uploaded file found ({os.path.getsize(path) // 1024} KB): {c.source_name}')
        c.ai_status, c.ai_error = 'working', ''
        c.save()
        try:
            data = open(path, 'rb').read()
            t = time.time()
            png, desc = ai.read_upload(data, '', c.source_name or path)
            self.ok(f"read the upload ({'with a page picture' if png else 'text only, no picture'}, "
                    f"{len(desc)} chars of text) in {time.time() - t:.1f}s")
            t = time.time()
            self.stdout.write('        asking OpenAI… (usually 20–90 s)')
            raw = ai.generate_html(png, desc)
            self.ok(f'OpenAI replied in {time.time() - t:.0f}s ({len(raw)} chars)')
            html = ai.sanitize(raw)
            self.ok('safety check passed')
            from apps.invoices.exact_template import SAMPLE
            ai.render(html, ai.ai_context(SAMPLE))
            self.ok('test render with sample data passed')
            c.ai_html, c.ai_status, c.ai_error = html, 'ready', ''
            c.ai_fields = ai.extract_fields(html)
            c.save()
            self.stdout.write(self.style.SUCCESS(
                f"\nDone — {len(c.ai_fields)} editable field(s). Refresh Add Invoice and click View."))
        except Exception as exc:
            c.ai_status, c.ai_error = 'failed', str(exc)[:500]
            c.save()
            self.bad(f'{type(exc).__name__}: {exc}')
