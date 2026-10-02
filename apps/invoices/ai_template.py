"""AI-designed templates (OpenAI).

The superadmin uploads an invoice design (PDF, image or Word). The whole
document is sent to an OpenAI vision model, which writes an HTML page that
recreates the design, with Django template variables in place of the sample
values. That HTML is checked (`sanitize`) — it runs on the server for every
invoice, so only a small, safe subset of the template language is allowed —
test-rendered, and stored on the CustomTemplate.

Configuration (.env):
    OPENAI_API_KEY   required — without it uploads use the non-AI paths
    OPENAI_MODEL     optional, default gpt-4.1 (must accept image input)
"""
import base64
import io
import logging
import os
import re
import threading
import zipfile
from datetime import datetime

from django.conf import settings
from django.template import Context, Engine

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'gpt-4.1'

# Variables the generated template may use. Item rows loop over `items`.
VARIABLES = {
    'seller_name': 'your company name', 'seller_address': 'your address',
    'seller_email': 'your email', 'seller_phone': 'your phone', 'seller_website': 'your website',
    'seller_gst': 'your GSTIN (value only)', 'seller_pan': 'your PAN', 'seller_cin': 'your CIN',
    'logo_url': 'URL of your logo image (may be empty)',
    'invoice_number': 'invoice number', 'invoice_date': 'invoice date', 'due_date': 'due date',
    'customer_name': 'customer / bill-to name', 'customer_contact': 'contact person',
    'customer_address': 'customer address', 'customer_email': 'customer email',
    'customer_phone': 'customer phone', 'customer_gst': 'customer GSTIN', 'customer_pan': 'customer PAN',
    'customer_cin': 'customer CIN',
    'subtotal': 'subtotal (formatted, e.g. ₹37,500.00)', 'cgst': 'CGST amount', 'sgst': 'SGST amount',
    'igst': 'IGST amount', 'cgst_label': 'e.g. "CGST @9%"', 'sgst_label': 'e.g. "SGST @9%"',
    'igst_label': 'e.g. "IGST @18%"', 'total_tax': 'total GST amount', 'grand_total': 'grand total',
    'amount_in_words': 'amount in words', 'notes': 'notes', 'terms': 'terms & conditions',
    'signatory_name': 'authorised signatory name', 'signed_on': 'signing date/time',
    'signature_url': 'URL of the signature image (may be empty)',
}
ITEM_FIELDS = {'sno': 'serial number', 'desc': 'description', 'hsn': 'HSN/SAC', 'qty': 'quantity',
               'unit': 'unit', 'rate': 'rate (formatted)', 'discount': 'discount % (may be empty)',
               'amount': 'line amount (formatted)'}

_ALLOWED_TAGS = {'for', 'endfor', 'empty', 'if', 'elif', 'else', 'endif', 'with', 'endwith'}
# Harmless text filters the model may use. Anything else is removed from the
# expression (the design still works, the value just isn't transformed) — except
# _DANGEROUS ones, which can emit raw HTML/script and reject the design.
_ALLOWED_FILTERS = {'default', 'default_if_none', 'upper', 'lower', 'title', 'capfirst', 'linebreaksbr',
                    'linebreaks', 'length', 'first', 'last', 'slice', 'truncatechars', 'truncatewords',
                    'cut', 'add', 'join', 'yesno', 'floatformat', 'center', 'ljust', 'rjust',
                    'wordwrap', 'striptags', 'stringformat', 'pluralize'}
_DANGEROUS_FILTERS = {'safe', 'safeseq', 'json_script', 'escapejs', 'force_escape', 'unordered_list',
                      'urlize', 'urlizetrunc', 'pprint'}


class AIError(RuntimeError):
    pass


def is_configured():
    return bool(os.environ.get('OPENAI_API_KEY', '').strip())


# ── reading the upload ──────────────────────────────────────────────────────

def _docx_as_html(data):
    """Word → a plain HTML rendering of its content and styling, so the model
    can read tables, shading, colours and bold even without a page image."""
    import xml.etree.ElementTree as ET
    W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            parts = [z.read('word/document.xml')]
            parts += [z.read(n) for n in z.namelist() if re.match(r'word/(header|footer)\d*\.xml$', n)]
            has_images = any(n.startswith('word/media/') for n in z.namelist())
    except Exception:
        raise AIError("Couldn't open that Word file. Save it as .docx or PDF and try again.")

    def run_html(r):
        t = ''.join(x.text or '' for x in r.iter(W + 't'))
        if not t:
            return ''
        rpr = r.find(W + 'rPr')
        st, bold = [], False
        if rpr is not None:
            c = rpr.find(W + 'color')
            if c is not None and re.fullmatch(r'[0-9A-Fa-f]{6}', c.get(W + 'val') or ''):
                st.append('color:#' + c.get(W + 'val'))
            bold = rpr.find(W + 'b') is not None
            sz = rpr.find(W + 'sz')
            if sz is not None and (sz.get(W + 'val') or '').isdigit():
                st.append(f"font-size:{int(sz.get(W + 'val')) / 2}pt")
        t = t.replace('&', '&amp;').replace('<', '&lt;')
        if bold:
            t = f'<b>{t}</b>'
        return f'<span style="{";".join(st)}">{t}</span>' if st else t

    def shade(el):
        shd = el.find(f'.//{W}shd') if el is not None else None
        v = shd.get(W + 'fill') if shd is not None else None
        return f'background:#{v}' if v and re.fullmatch(r'[0-9A-Fa-f]{6}', v) else ''

    def para(p):
        ppr = p.find(W + 'pPr')
        jc = ppr.find(W + 'jc') if ppr is not None else None
        align = f"text-align:{jc.get(W + 'val')}" if jc is not None and jc.get(W + 'val') in ('center', 'right') else ''
        style = ';'.join(x for x in (shade(ppr), align) if x)
        inner = ''.join(run_html(r) for r in p.iter(W + 'r'))
        return f'<p style="{style}">{inner}</p>' if inner.strip() else ''

    def block(el):
        if el.tag == W + 'p':
            return para(el)
        if el.tag == W + 'tbl':
            rows = []
            for tr in el.findall(W + 'tr'):
                cells = []
                for tc in tr.findall(W + 'tc'):
                    tcp = tc.find(W + 'tcPr')
                    span = tcp.find(W + 'gridSpan') if tcp is not None else None
                    cs = f' colspan="{span.get(W + "val")}"' if span is not None else ''
                    cells.append(f'<td{cs} style="{shade(tcp)}">' + ''.join(block(c) for c in tc) + '</td>')
                rows.append('<tr>' + ''.join(cells) + '</tr>')
            return '<table border="1">' + ''.join(rows) + '</table>'
        return ''

    out = []
    for raw in parts:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        # Word keeps a page colour (e.g. a dark page) outside the body.
        bg = root.find(W + 'background')
        if bg is not None and re.fullmatch(r'[0-9A-Fa-f]{6}', bg.get(W + 'color') or ''):
            out.append(f'<!-- the whole page background colour is #{bg.get(W + "color")} -->')
        body = root.find(W + 'body') if root.find(W + 'body') is not None else root
        out.append(''.join(block(el) for el in body))
    html = '\n'.join(out)
    if has_images:
        html = '<!-- the document contains an image near the top (a logo) -->\n' + html
    return html


def _docx_to_pdf(data):
    """Convert Word → PDF with LibreOffice when it's installed. Returns bytes or None."""
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which('soffice') or shutil.which('libreoffice')
    if not exe:
        return None
    tmp = tempfile.mkdtemp(prefix='docx2pdf_')
    try:
        src = os.path.join(tmp, 'design.docx')
        with open(src, 'wb') as fh:
            fh.write(data)
        subprocess.run([exe, '--headless', '--norestore', f'-env:UserInstallation=file://{tmp}/profile',
                        '--convert-to', 'pdf', '--outdir', tmp, src],
                       capture_output=True, timeout=120)
        out = os.path.join(tmp, 'design.pdf')
        if os.path.exists(out) and os.path.getsize(out) > 0:
            with open(out, 'rb') as fh:
                return fh.read()
    except Exception:
        logger.warning("LibreOffice conversion failed", exc_info=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


def read_upload(data, content_type, filename):
    """Return (png_bytes_or_None, text_description) for the model."""
    name = (filename or '').lower()
    if name.endswith('.docx'):
        # Give the model a real picture of the Word page when LibreOffice is
        # available; without one it only sees structure and guesses the look.
        pdf = _docx_to_pdf(data)
        if pdf:
            png, text = read_upload(pdf, 'application/pdf', 'design.pdf')
            return png, (text + "\n\nThe same design as Word-HTML (exact colours and shading):\n"
                         + _docx_as_html(data))
    if name.endswith('.pdf') or content_type == 'application/pdf':
        import fitz
        doc = fitz.open(stream=data, filetype='pdf')
        page = doc[0]
        png = page.get_pixmap(dpi=110).tobytes('png')
        return png, "Text on the page (top to bottom):\n" + (page.get_text() or '')
    if name.endswith('.docx'):
        return None, "The design, converted from Word to HTML (structure, shading, colours and bold are real):\n" + _docx_as_html(data)
    if name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert('RGB')
        img.thumbnail((1400, 2000))
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue(), ''
    raise AIError("Upload a Word (.docx), PDF or image of the invoice design.")


# ── prompting ───────────────────────────────────────────────────────────────

def _prompt(description):
    vars_ = '\n'.join(f'  {{{{ {k} }}}} — {v}' for k, v in VARIABLES.items())
    item_vars = ', '.join(f'it.{k} ({v})' for k, v in ITEM_FIELDS.items())
    return f"""You are recreating an invoice design as an HTML template for an invoicing app.

Study the attached invoice design carefully and reproduce it as faithfully as you can.
If a picture of the page is attached, THE PICTURE IS THE SOURCE OF TRUTH: match what you see —
the page background colour (e.g. a dark/black page with light text), where the logo sits
(left/right), which side the company name and GSTIN/PAN are on, table border colours, label
colours (e.g. red labels), band colours, alignment and spacing. Do not invent a different layout
or add sections that are not in the design. Keep:
the same layout, sections and their order, colours (exact hex values), header/footer bands,
table structure and borders, alignment, font weights and relative sizes, spacing, and every
fixed label and heading (e.g. "TAX INVOICE", "Bill To", "Authorised Signatory", bank details,
declarations, footer address). Fixed text that is part of the business's design stays as text.

Replace every SAMPLE VALUE (names, addresses, numbers, dates, amounts, item lines) with the
matching variable below, written in Django template syntax:
{vars_}
Line items: loop over them exactly once —
  {{% for it in items %}} … {{% endfor %}}   using {item_vars}.
Optional values can be guarded: {{% if customer_gst %}} … {{% endif %}}.
Logo: if the design shows a logo, use <img src="{{{{ logo_url }}}}" …> wrapped in {{% if logo_url %}}…{{% endif %}}.

Hard rules — the output is checked automatically and rejected otherwise:
- Output ONLY the HTML. No markdown, no code fences, no explanations.
- One root element: <div class="ai-page"> … </div>. Put all CSS in a single <style> block inside it,
  every selector prefixed with .ai-page. No <script>, no event handlers, no external fonts/URLs/@import.
- Page margins like a printed invoice: keep all content inside, with space above the header and
  below the footer, and never let tables or bands touch the left/right page edge (the app also adds
  padding of about 38px top, 42px sides, 34px bottom to .ai-page — design for that inner area).
- The page is A4: .ai-page {{ width: 794px; min-height: 1123px; box-sizing: border-box; }} with
  background set to the design's own page colour (dark if the design is dark), plus
  -webkit-print-color-adjust: exact; print-color-adjust: exact; so it prints.
- Use px units. Fonts: ONLY "Arial, Helvetica, sans-serif" (or "Georgia, 'Times New Roman', serif" if the
  design is clearly serif). Never name any other font. No text-transform or font-variant unless the design
  itself shows that text in capitals.
- Use only the variables listed. Only these template tags: for, endfor, empty, if, elif, else, endif.
  Filters: keep to default, upper, lower, title, linebreaksbr (others are removed). Never use the safe filter.
- Images: only {{{{ logo_url }}}} or {{{{ signature_url }}}} as src.
- Make it print well: use real <table> elements for tabular parts; avoid position:fixed.
{('' if not description else chr(10) + description[:30000])}"""


def generate_html(png, description):
    """Call OpenAI and return the raw HTML it produced."""
    try:
        from openai import OpenAI
    except ImportError:
        raise AIError("The openai package isn't installed on the server (pip install openai).")
    if not is_configured():
        raise AIError("OPENAI_API_KEY is not set in .env.")
    client = OpenAI(timeout=180)
    content = [{'type': 'text', 'text': _prompt(description)}]
    if png:
        content.append({'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(png).decode(), 'detail': 'high'}})
    try:
        resp = client.chat.completions.create(
            model=os.environ.get('OPENAI_MODEL', '').strip() or DEFAULT_MODEL,
            messages=[{'role': 'user', 'content': content}],
            temperature=0.2,
            max_completion_tokens=12000,
        )
    except Exception as exc:
        raise AIError(f"OpenAI request failed: {exc}")
    text = (resp.choices[0].message.content or '').strip()
    if resp.choices[0].finish_reason == 'length':
        raise AIError("The design was too long for one response. Try a simpler page or a PDF of page 1 only.")
    return text


# ── safety ──────────────────────────────────────────────────────────────────

def sanitize(html):
    """Strip fences and enforce the allowed template subset. Raises AIError."""
    html = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', html.strip())
    start = html.find('<div')
    if start < 0 or 'ai-page' not in html:
        raise AIError("The AI didn't return a page. Try again.")
    html = html[start:]
    bad = [
        (r'<\s*(script|iframe|object|embed|link|meta|base|form|input|button|textarea|svg)\b', 'disallowed element'),
        (r'\son[a-z]+\s*=', 'event handler'),
        (r'javascript\s*:', 'javascript URL'),
        (r'@import', '@import'),
        (r'url\(\s*[\'"]?(?!data:)', 'external CSS URL'),
        (r'\|\s*safe\b|\|\s*safeseq\b', 'safe filter'),
        (r'expression\s*\(', 'CSS expression'),
    ]
    for pat, why in bad:
        if re.search(pat, html, re.I):
            raise AIError(f"The generated design was rejected ({why}). Try again.")
    for tag in re.findall(r'{%\s*(\w+)', html):
        if tag not in _ALLOWED_TAGS:
            raise AIError(f"The generated design used a template tag that isn't allowed ({tag}). Try again.")
    def _filters(m):
        expr = m.group(0)
        for flt in re.findall(r'\|\s*(\w+)', expr):
            if flt in _DANGEROUS_FILTERS:
                raise AIError(f"The generated design was rejected ({flt} filter). Try again.")
        # Drop unknown filters (with their argument) instead of failing the whole design.
        return re.sub(r'\|\s*(\w+)(\s*:\s*("[^"]*"|\'[^\']*\'|[\w.]+))?',
                      lambda f: f.group(0) if f.group(1) in _ALLOWED_FILTERS else '', expr)
    html = re.sub(r'{{.*?}}|{%.*?%}', _filters, html, flags=re.S)
    allowed_vars = set(VARIABLES) | {'items', 'it', 'forloop'}
    for expr in re.findall(r'{{\s*([^}|]+)', html) + re.findall(r'{%\s*(?:if|elif|for\s+\w+\s+in|with)\s+([^%]+)%}', html):
        for name in re.findall(r'[A-Za-z_][A-Za-z_0-9]*', expr):
            root = name
            if root in ('and', 'or', 'not', 'in', 'is', 'None', 'True', 'False', 'it') or root in allowed_vars:
                continue
            if root in ITEM_FIELDS or root in ('counter', 'counter0', 'first', 'last'):
                continue
            raise AIError(f"The generated design used an unknown value ({root}). Try again.")
    for src in re.findall(r'src\s*=\s*["\']([^"\']*)', html, re.I):
        if not re.fullmatch(r'\s*({{\s*(logo_url|signature_url)\s*}}|data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+)\s*', src):
            raise AIError("The generated design pointed an image at an outside address. Try again.")
    return html


# Default tags/filters are needed for for/if; sanitize() already limits which may appear.
_ENGINE = Engine(debug=False, libraries={})


# Page margins applied to every AI design, whatever the model wrote: content
# keeps ~10mm top/bottom and ~11mm left/right from the sheet edge, while the
# page background (e.g. a dark design) still fills the whole A4 sheet.
_PAGE_FRAME = ('<style>.ai-page{width:794px !important;min-height:1123px;box-sizing:border-box !important;'
               'padding:38px 42px 34px !important;margin:0 auto;'
               '-webkit-print-color-adjust:exact;print-color-adjust:exact}'
               '.ai-page>*:first-child{margin-top:0}.ai-page>*:last-child{margin-bottom:0}</style>')


def render(html, ctx):
    """Render a sanitized AI template with an ai_context() dict."""
    return _ENGINE.from_string(html).render(Context(ctx, autoescape=True)) + _PAGE_FRAME


# ── context ─────────────────────────────────────────────────────────────────

def ai_context(values):
    """exact_template.values_from_context()/SAMPLE dict → the AI variable names."""
    m = {
        'seller_name': 'seller.name', 'seller_address': 'seller.address', 'seller_email': 'seller.email',
        'seller_phone': 'seller.phone', 'seller_website': 'seller.website', 'seller_gst': 'seller.gst',
        'seller_pan': 'seller.pan', 'seller_cin': 'seller.cin', 'invoice_number': 'inv.number',
        'invoice_date': 'inv.date', 'due_date': 'inv.due', 'customer_name': 'cust.name',
        'customer_contact': 'cust.recipient', 'customer_address': 'cust.address', 'customer_email': 'cust.email',
        'customer_phone': 'cust.phone', 'customer_gst': 'cust.gst', 'customer_pan': 'cust.pan',
        'customer_cin': 'cust.cin', 'subtotal': 'tot.subtotal', 'cgst': 'tot.cgst', 'sgst': 'tot.sgst',
        'igst': 'tot.igst', 'cgst_label': 'tot.cgst_lbl', 'sgst_label': 'tot.sgst_lbl', 'igst_label': 'tot.igst_lbl',
        'total_tax': 'tot.tax', 'grand_total': 'tot.grand', 'amount_in_words': 'tot.words', 'notes': 'note.notes',
        'terms': 'note.terms', 'signatory_name': 'sig.name', 'signed_on': 'sig.datetime',
    }
    out = {k: ('' if values.get(v) is None else str(values.get(v))) for k, v in m.items()}
    out['logo_url'] = values.get('_logo') or ''
    out['signature_url'] = values.get('_sig') or ''
    out['items'] = [{
        'sno': it.get('item.sno', ''), 'desc': it.get('item.desc', ''), 'hsn': it.get('item.hsn', ''),
        'qty': it.get('item.qty', ''), 'unit': it.get('item.unit', ''), 'rate': it.get('item.rate', ''),
        'discount': it.get('item.disc', ''), 'amount': it.get('item.amount', ''),
    } for it in values.get('_items') or []]
    return out


# ── background job ──────────────────────────────────────────────────────────

def _job(template_id):
    from .models import CustomTemplate
    from .exact_template import SAMPLE
    c = CustomTemplate.objects(template_id=template_id).first()
    if not c:
        return
    try:
        path = os.path.join(settings.MEDIA_ROOT, c.source_path)
        with open(path, 'rb') as fh:
            data = fh.read()
        png, desc = read_upload(data, '', c.source_name or path)
        html = sanitize(generate_html(png, desc))
        render(html, ai_context(SAMPLE))        # must render cleanly with sample data
        c.ai_html, c.ai_status, c.ai_error = html, 'ready', ''
    except AIError as exc:
        c.ai_status, c.ai_error = 'failed', str(exc)[:500]
    except Exception as exc:
        logger.warning("AI template job failed for %s", template_id, exc_info=True)
        c.ai_status, c.ai_error = 'failed', f"Couldn't build the design ({type(exc).__name__}). Try again."
    c.save()


def start_job(template_id):
    """Generate in a background thread — it takes longer than a web request may."""
    threading.Thread(target=_job, args=(template_id,), daemon=True).start()
