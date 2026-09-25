"""Invoice template registry — single source of truth for template ids,
metadata, default plan gates and section definitions.

Plan gating resolution order (per template):
    1. TemplateConfig document (superadmin override via /z-admin/)
    2. DEFAULT_MIN_PLAN below

Blocking: a template present in TemplateBlock is unavailable to everyone.
"""

# Canonical section ids — used by layout_config.section_order / hidden_sections
SECTIONS = [
    'header',        # masthead / identity block
    'invoice_meta',  # invoice number, dates, status
    'bill_to',       # from + bill-to parties
    'items_table',
    'totals',
    'notes',
    'terms',
    'signature',
    'footer',
]

SECTION_LABELS = {
    'header':       'Header',
    'invoice_meta': 'Invoice Details',
    'bill_to':      'From & Bill To',
    'items_table':  'Items Table',
    'totals':       'Totals',
    'notes':        'Notes',
    'terms':        'Terms & Conditions',
    'signature':    'Signature',
    'footer':       'Footer',
}

PLAN_RANK = {'free': 0, 'plus': 1, 'pro': 2, 'unlimited': 3, 'premium': 3}

# Task categories — what kind of document each template produces. The picker
# shows this tag so users can match a template to their job (billing, payroll…).
TASK_LABELS = {
    'billing':  'Billing',
    'staffing': 'Staffing / HR',
    'payroll':  'Payroll',
}

# id -> definition. Order = display order in the picker.
INVOICE_TEMPLATES = [
    {
        'id': 'classic',
        'name': 'Default',
        'desc': 'Two-column header, ruled lines, zebra rows — trusted document feel',
        'min_plan': 'free',
        'badge': '#F8FAFC',
        'task': 'billing',
    },
    {
        'id': 'minimal',
        'name': 'Minimal',
        'desc': 'Typography-first, hairline rules, zero color fills',
        'min_plan': 'free',
        'badge': '#FFFFFF',
        'task': 'billing',
    },
    {
        'id': 'forest',
        'name': 'Classic (Green)',
        'desc': 'Deep green accent on the classic layout — the free generator default',
        'min_plan': 'free',
        'badge': '#ECFDF5',
        'task': 'billing',
    },
    {
        'id': 'ocean',
        'name': 'Professional (Blue)',
        'desc': 'Blue accent on the classic layout — calm, corporate finish',
        'min_plan': 'free',
        'badge': '#EFF6FF',
        'task': 'billing',
    },
    {
        'id': 'slate',
        'name': 'Minimal (Clean)',
        'desc': 'Grey accent on the classic layout — understated and neutral',
        'min_plan': 'free',
        'badge': '#F8FAFC',
        'task': 'billing',
    },
    {
        'id': 'crimson',
        'name': 'Modern (Red)',
        'desc': 'Red accent on the classic layout — high-contrast and assertive',
        'min_plan': 'free',
        'badge': '#FEF2F2',
        'task': 'billing',
    },
    {
        'id': 'violet',
        'name': 'Elegant (Purple)',
        'desc': 'Purple accent on the classic layout — distinctive without shouting',
        'min_plan': 'free',
        'badge': '#F5F3FF',
        'task': 'billing',
    },
    {
        'id': 'modern',
        'name': 'Modern',
        'desc': 'Dark full-width masthead band with accent underline',
        'min_plan': 'plus',
        'badge': '#0F172A',
        'task': 'billing',
    },
    {
        'id': 'professional',
        'name': 'Professional',
        'desc': 'Accent sidebar strip, warm tinted info band',
        'min_plan': 'pro',
        'badge': '#FFF7F0',
        'task': 'billing',
    },
    {
        'id': 'bold',
        'name': 'Bold',
        'desc': 'Full-bleed color header, giant type, colored footer band',
        'min_plan': 'pro',
        'badge': '#E87A3D',
        'task': 'billing',
    },
    {
        'id': 'compact',
        'name': 'Compact',
        'desc': 'Data-dense single-page format for long line-item lists',
        'min_plan': 'pro',
        'badge': '#F1F5F9',
        'task': 'billing',
    },
    {
        'id': 'sidebar',
        'name': 'Sidebar',
        'desc': 'Full-height color panel carrying your business identity',
        'min_plan': 'pro',
        'badge': '#1D4ED8',
        'task': 'billing',
    },
    {
        'id': 'boxed',
        'name': 'Card',
        'desc': 'Centered masthead with rounded boxed sections',
        'min_plan': 'pro',
        'badge': '#ECFDF5',
        'task': 'billing',
    },
    {
        'id': 'statement',
        'name': 'Statement',
        'desc': 'Creative editorial layout with oversized typography',
        'min_plan': 'pro',
        'badge': '#0F172A',
        'task': 'billing',
    },
    {
        'id': 'receipt',
        'name': 'Receipt',
        'desc': 'Narrow shop-counter bill — centred store header, dashed rules, item lines and a big total',
        'min_plan': 'plus',
        'badge': '#FFFBEB',
        'task': 'billing',
    },
    {
        'id': 'staffing',
        'name': 'Staffing',
        'desc': 'Professional services invoice with employee, role and working-days columns',
        'min_plan': 'pro',
        'badge': '#9CC2E4',
        'task': 'staffing',
    },
    {
        'id': 'payroll',
        'name': 'Payroll',
        'desc': 'Salary & payroll summary — employee, designation, pay period, days paid, net pay',
        'min_plan': 'pro',
        'badge': '#CCFBF1',
        'task': 'payroll',
    },
]

TEMPLATE_IDS = [t['id'] for t in INVOICE_TEMPLATES]
TEMPLATE_MAP = {t['id']: t for t in INVOICE_TEMPLATES}


# ── TEMPLATE_SPECS — single source of visual truth ──────────────────────────
# The ONLY place colors/fonts/layout-flavour for a template are defined.
# Consumed by: pdf_generator.py (ReportLab colors/fonts), free_views.py
# (via pdf_generator), and form.html (as CSS custom properties for the
# live web preview). Do not hardcode per-template hex values anywhere else.
#
# Fields:
#   accent   - primary brand/accent color (bars, badges, highlights)
#   secondary- dark/band color used for masthead bands, dark totals bars
#   text     - main body/heading text color
#   muted    - secondary/label text color
#   border   - hairline/rule/box border color
#   tint     - light tinted background for info panels/address blocks
#   heading_font / body_font - ReportLab-safe font family names
#   header_style - short label describing the header layout flavour
#                  (metadata only — actual layout code lives in pdf_generator's
#                  per-template section builders and form.html's DOM)
TEMPLATE_SPECS = {
    'classic': {
        'accent': '#E87A3D', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F8FAFC',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'two-column, logo right, ruled underline',
    },
    'minimal': {
        'accent': '#0F172A', 'secondary': '#111827', 'text': '#111827',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#FFFFFF',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'typography-first, hairline rule, no fills',
    },
    'forest': {
        'accent': '#1A3A2A', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F0FDF4',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'deep green accent, classic two-column header',
    },
    'ocean': {
        'accent': '#2563EB', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#EFF6FF',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'blue accent, classic two-column header',
    },
    'slate': {
        'accent': '#64748B', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F8FAFC',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'grey accent, classic two-column header',
    },
    'crimson': {
        'accent': '#C1121F', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#FEF2F2',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'red accent, classic two-column header',
    },
    'violet': {
        'accent': '#7C3AED', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F5F3FF',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'purple accent, classic two-column header',
    },
    'modern': {
        'accent': '#E87A3D', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#94A3B8', 'border': '#E2E8F0', 'tint': '#F8FAFC',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'dark full-width masthead band, accent underline',
    },
    'professional': {
        'accent': '#E87A3D', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#FDDCBB', 'tint': '#FFF7F0',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'accent sidebar strip, warm tinted info band',
    },
    'bold': {
        'accent': '#E87A3D', 'secondary': '#1E293B', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#FFF7F0',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'full-bleed color header, giant type, colored footer band',
    },
    'compact': {
        'accent': '#E87A3D', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F1F5F9',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'condensed single-line header, dense table rows',
    },
    'sidebar': {
        'accent': '#E87A3D', 'secondary': '#16243D', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F4F7FB',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'full-height color identity panel, left column',
    },
    'boxed': {
        'accent': '#065F46', 'secondary': '#065F46', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#A7F3D0', 'tint': '#ECFDF5',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'centered masthead, rounded boxed sections',
    },
    'statement': {
        'accent': '#E87A3D', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E2E8F0', 'tint': '#F8FAFC',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'editorial, oversized type, asymmetric balance block',
    },
    'receipt': {
        'accent': '#111827', 'secondary': '#111827', 'text': '#111827',
        'muted': '#4B5563', 'border': '#9CA3AF', 'tint': '#FFFFFF',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'narrow till-receipt column, centred store header, dashed rules',
    },
    # Editorial orange/white treatment, sampled from the reference invoice
    # (inv00123.pdf): #F97316 accent, #FFFBEB amount-in-words band with #92400E
    # text, #0F172A body, #64748B muted, #E5E7EB hairlines.
    'staffing': {
        'accent': '#F97316', 'secondary': '#0F172A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E5E7EB', 'tint': '#FFF7ED',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'editorial header, orange accents, employee-role-days table',
    },
    # Payroll shares the staffing document engine (same section builder and
    # preview skin) with payroll-specific labels: pay period, designation,
    # days paid, net pay.
    'payroll': {
        'accent': '#0F766E', 'secondary': '#134E4A', 'text': '#0F172A',
        'muted': '#64748B', 'border': '#E5E7EB', 'tint': '#F0FDFA',
        'heading_font': 'Helvetica-Bold', 'body_font': 'Helvetica',
        'header_style': 'editorial header, teal accents, employee-designation-payperiod table',
    },
}


def get_template_spec(template_id):
    """Visual spec for a template id, falling back to 'classic'."""
    return TEMPLATE_SPECS.get(template_id) or TEMPLATE_SPECS['classic']


def template_specs_payload():
    """JSON-serializable {id: spec} for the web preview (form.html)."""
    return dict(TEMPLATE_SPECS)


def get_template_def(template_id):
    return TEMPLATE_MAP.get(template_id)


def effective_min_plan(template_id):
    """Registry default overridden by superadmin's TemplateConfig doc."""
    from apps.authentication.models import TemplateConfig
    cfg = TemplateConfig.objects(template_id=template_id).first()
    if cfg:
        return cfg.min_plan
    return TEMPLATE_MAP[template_id]['min_plan'] if template_id in TEMPLATE_MAP else 'free'


def is_blocked(template_id):
    from apps.authentication.models import TemplateBlock
    return TemplateBlock.objects(template_id=template_id).first() is not None


def plan_allows(plan, template_id):
    """plan: subscription slug ('free'|'plus'|'pro'|'unlimited'|'premium') or role bypass handled by caller."""
    if template_id not in TEMPLATE_MAP:
        return False
    if is_blocked(template_id):
        return False
    user_rank = PLAN_RANK.get(plan, 0)
    need_rank = PLAN_RANK.get(effective_min_plan(template_id), 0)
    return user_rank >= need_rank


def resolve_template(template_id, plan):
    """Return a usable template id for the given plan; fall back to first allowed."""
    if template_id in TEMPLATE_MAP and plan_allows(plan, template_id):
        return template_id
    # Prefer the requested tier's free fallback chain
    for tid in TEMPLATE_IDS:
        if plan_allows(plan, tid):
            return tid
    return 'classic'


def templates_payload():
    """Static metadata for admin UI / docs."""
    out = []
    for t in INVOICE_TEMPLATES:
        d = dict(t)
        d['effective_min_plan'] = effective_min_plan(t['id'])
        d['blocked'] = is_blocked(t['id'])
        d['task_label'] = TASK_LABELS.get(t.get('task'), 'Billing')
        out.append(d)
    return out


def normalize_layout_config(layout_config):
    """Fill defaults, merge legacy flat toggles, validate section order.

    Returns canonical dict:
    {
      section_order: [...], hidden_sections: [...],
      section_settings: {items_table:{show_hsn,show_discount}, header:{show_gstin}, bill_to:{show_gst}},
      logo_width: int px 60..200
    }
    """
    lc = dict(layout_config or {})

    raw_order = lc.get('section_order') or []
    order = [s for s in raw_order if s in SECTIONS]
    for s in SECTIONS:
        if s not in order:
            order.append(s)

    hidden = [s for s in (lc.get('hidden_sections') or []) if s in SECTIONS]

    ss_raw = lc.get('section_settings') or {}
    items = ss_raw.get('items_table') or {}
    header = ss_raw.get('header') or {}
    bill_to = ss_raw.get('bill_to') or {}
    # Legacy flat keys
    section_settings = {
        'items_table': {
            'show_hsn':      bool(items.get('show_hsn', lc.get('show_hsn', True))),
            'show_discount': bool(items.get('show_discount', lc.get('show_discount', True))),
        },
        'header':  {'show_gstin': bool(header.get('show_gstin', True))},
        'bill_to': {'show_gst':   bool(bill_to.get('show_gst', True))},
    }

    try:
        logo_width = int(lc.get('logo_width', 110))
    except (TypeError, ValueError):
        logo_width = 110
    logo_width = max(60, min(200, logo_width))

    # Legacy flat toggles kept for backward compatibility
    legacy = {k: lc[k] for k in ('show_terms', 'show_signature', 'show_watermark') if k in lc}
    # Preview-only for now (the PDF builders don't read them yet); stored so a
    # later PDF pass can honour them without a data migration.
    if isinstance(lc.get('swap_parties'), bool):
        legacy['swap_parties'] = lc['swap_parties']
    labels = lc.get('custom_labels')
    if isinstance(labels, dict):
        legacy['custom_labels'] = {str(k)[:40]: str(v)[:60] for k, v in list(labels.items())[:20]}
    # Renamed field labels (e.g. GSTIN -> GST) from the free generator's own
    # form fields — read by pdf_generator.py's classic/minimal builders,
    # keyed by the source input's id (fi-from-gst, fi-inv-number, ...).
    field_labels = lc.get('field_labels')
    if isinstance(field_labels, dict):
        legacy['field_labels'] = {str(k)[:40]: str(v)[:60] for k, v in list(field_labels.items())[:20]}

    cfg = {
        'section_order': order,
        'hidden_sections': hidden,
        'section_settings': section_settings,
        'logo_width': logo_width,
    }
    cfg.update(legacy)
    return cfg
