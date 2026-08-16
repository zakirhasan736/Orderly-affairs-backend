"""Shared HTML email layout for Orderly Affairs.

Paper / ink brand system for reminder + transactional mail.
Email-safe tables only (no flex). Fluid max-width — never fixed mock widths.
"""

from __future__ import annotations

import html
from typing import Iterable, Mapping, Sequence

from app.config import settings

# Legacy navy (kept for any remaining call sites)
BRAND_NAVY = "#10213f"
BRAND_NAVY_SOFT = "#1a335f"
BRAND_MUTED = "#64748b"
BRAND_BORDER = "#e2e8f0"
BRAND_BG = "#f4f6f9"
BRAND_CARD = "#ffffff"
BRAND_ACCENT = "#2563eb"

# Paper / ink (reminder templates + preferred shell)
INK = "#132b26"
INK_SOFT = "#3c4a46"
INK_MUTED = "#8b9995"
INK_HINT = "#6e7c77"
PAPER = "#f2f1ec"
PAPER_SOFT = "#f7f6f2"
LINE = "#e4e6e1"
LINE_SOFT = "#f2f1ec"
WARN_INK = "#7a5a1c"
WARN_BORDER = "#e8d9b5"
DANGER = "#b4483f"
DANGER_BORDER = "#e5b6b0"
AMBER = "#8a6420"

FONT_SANS = (
    "'Instrument Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Helvetica,Arial,sans-serif"
)
FONT_SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif"
FONT_MONO = "'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace"

# Hosted brand mark for email headers (email clients cannot load localhost).
_DEFAULT_EMAIL_LOGO_URL = (
    "https://res.cloudinary.com/davvdgwe3/image/upload/v1784951738/orderly-affairs/brand-logo.png"
)


def brand_logo_url() -> str:
    """Public logo URL used in email headers (must be absolute HTTPS for clients)."""
    custom = (getattr(settings, "EMAIL_LOGO_URL", None) or "").strip()
    if custom:
        return custom
    return _DEFAULT_EMAIL_LOGO_URL


def email_brand_mark(
    *,
    box: int = 30,
    img: int = 22,
    class_box: str = "oa-logo-box",
    class_img: str = "oa-logo-img",
) -> str:
    """Brand logo on a white rounded tile (readable on paper headers)."""
    logo = escape(brand_logo_url())
    return f"""
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="{class_box}" style="width:{box}px; height:{box}px; background:#ffffff; border:1px solid {LINE}; border-radius:8px;">
                      <tr>
                        <td align="center" valign="middle" style="width:{box}px; height:{box}px;">
                          <img class="{class_img}" src="{logo}" width="{img}" height="{img}" alt="Orderly Affairs" style="display:block; width:{img}px; height:{img}px; border:0; outline:none; text-decoration:none;" />
                        </td>
                      </tr>
                    </table>
    """.strip()


def portal_url() -> str:
    return (settings.FRONTEND_URL or "https://vault.orderly-affairs.com").rstrip("/")


def billing_url() -> str:
    return f"{portal_url()}/dashboard"


def kit_url() -> str:
    return f"{portal_url()}/dashboard"


def access_url() -> str:
    return f"{portal_url()}/dashboard"


def escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def email_button(url: str, label: str) -> str:
    """Primary ink pill CTA (left-aligned, desktop-friendly)."""
    return email_pill_button(url, label, variant="primary")


def email_pill_button(
    url: str,
    label: str,
    *,
    variant: str = "primary",
    full_width: bool = False,
) -> str:
    safe_url = escape(url)
    safe_label = escape(label)
    if variant == "danger":
        bg, color, border = "#ffffff", DANGER, DANGER_BORDER
    elif variant == "secondary":
        bg, color, border = "#ffffff", INK, LINE
    else:
        bg, color, border = INK, "#ffffff", INK

    display = "block" if full_width else "inline-block"
    width = "width:100%; box-sizing:border-box; text-align:center;" if full_width else ""
    return f"""
    <a href="{safe_url}"
       style="display:{display}; {width} padding:13px 20px; border-radius:22px; background:{bg}; color:{color}; border:1px solid {border}; font-family:{FONT_SANS}; font-size:13.5px; font-weight:500; text-decoration:none; line-height:1.2;">
      {safe_label}
    </a>
    """


def email_cta_row(
    primary: tuple[str, str],
    secondary: tuple[str, str] | None = None,
    *,
    secondary_variant: str = "secondary",
) -> str:
    """Reminder CTAs: full-width stacked pills (mobile-first, fluid width)."""
    rows = [
        f"""<tr><td style="padding:0 0 {'10px' if secondary else '0'} 0;">
          {email_pill_button(primary[0], primary[1], variant="primary", full_width=True)}
        </td></tr>"""
    ]
    if secondary:
        rows.append(
            f"""<tr><td style="padding:0;">
              {email_pill_button(secondary[0], secondary[1], variant=secondary_variant, full_width=True)}
            </td></tr>"""
        )
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 0 0; width:100%;">
      {''.join(rows)}
    </table>
    """


def email_code_box(code: str | int) -> str:
    safe = escape(code)
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
      <tr>
        <td align="center" style="background:{PAPER_SOFT}; border:1px solid {LINE}; border-radius:14px; padding:22px 16px;">
          <p style="margin:0 0 8px 0; font-size:12px; font-weight:600; letter-spacing:0.12em; text-transform:uppercase; color:{INK_MUTED}; font-family:{FONT_MONO};">
            Verification code
          </p>
          <p style="margin:0; font-size:32px; font-weight:800; letter-spacing:0.28em; color:{INK}; font-family:{FONT_MONO};">
            {safe}
          </p>
        </td>
      </tr>
    </table>
    """


def email_info_rows(rows: Sequence[tuple[str, str]]) -> str:
    """Render a compact labeled detail list."""
    items = []
    for i, (label, value) in enumerate(rows):
        border = f"border-bottom:1px solid {LINE};" if i < len(rows) - 1 else ""
        items.append(
            f"""
            <tr>
              <td style="padding:10px 0; {border} font-family:{FONT_SANS};">
                <p style="margin:0 0 4px 0; font-size:11px; font-weight:500; letter-spacing:0.08em; text-transform:uppercase; color:{INK_MUTED}; font-family:{FONT_MONO};">{escape(label)}</p>
                <p style="margin:0; font-size:15px; font-weight:600; color:{INK}; line-height:1.5;">{escape(value)}</p>
              </td>
            </tr>
            """
        )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 4px 0;">
      {''.join(items)}
    </table>
    """


def email_expiry_rows(
    rows: Sequence[tuple[str, str, str]],
) -> str:
    """Expiry list: (section_code, label, date_text)."""
    if not rows:
        return ""
    items: list[str] = []
    for i, (code, label, date_text) in enumerate(rows):
        border = f"border-bottom:1px solid {LINE_SOFT};" if i < len(rows) - 1 else ""
        items.append(
            f"""
            <tr>
              <td style="padding:14px 16px; {border} vertical-align:middle; width:66px;">
                <span style="font-family:{FONT_MONO}; font-size:10px; font-weight:500; color:{INK_MUTED};">{escape(code)}</span>
              </td>
              <td style="padding:14px 8px; {border} vertical-align:middle; font-family:{FONT_SANS}; font-size:14px; color:{INK};">
                {escape(label)}
              </td>
              <td style="padding:14px 16px; {border} vertical-align:middle; text-align:right; white-space:nowrap; font-family:{FONT_SANS}; font-size:13px; font-weight:500; color:{AMBER};">
                {escape(date_text)}
              </td>
            </tr>
            """
        )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 0 0; border:1px solid {LINE_SOFT}; border-radius:11px; overflow:hidden;">
      {''.join(items)}
    </table>
    """


def email_chips(labels: Sequence[str]) -> str:
    if not labels:
        return ""
    cells = [
        f"""<td style="padding:0 8px 8px 0;">
          <span style="display:inline-block; font-family:{FONT_SANS}; font-size:12.5px; color:{INK_SOFT}; background:{PAPER_SOFT}; border-radius:6px; padding:7px 11px;">{escape(label)}</span>
        </td>"""
        for label in labels
    ]
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 0 0;">
      <tr>{''.join(cells)}</tr>
    </table>
    """


def email_callout(text: str, *, tone: str = "info") -> str:
    tones = {
        "info": (PAPER_SOFT, LINE, INK),
        "warning": ("#fdf8ee", WARN_BORDER, WARN_INK),
        "danger": ("#fdf4f3", DANGER_BORDER, DANGER),
        "success": ("#ecfdf5", "#a7f3d0", "#065f46"),
    }
    bg, border, color = tones.get(tone, tones["info"])
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0;">
      <tr>
        <td style="background:{bg}; border:1px solid {border}; border-radius:12px; padding:14px 16px; font-family:{FONT_SANS}; font-size:14px; line-height:1.6; color:{color};">
          {text}
        </td>
      </tr>
    </table>
    """


def p(text: str) -> str:
    """Paragraph with standard body styling. ``text`` may include safe HTML."""
    return f"""
    <p style="margin:0 0 14px 0; font-family:{FONT_SANS}; font-size:14.5px; line-height:1.7; color:{INK_SOFT};">
      {text}
    </p>
    """


def greeting(name: str | None = None) -> str:
    label = escape(name) if name and str(name).strip() else "there"
    return p(f"Hello {label},")


def schedule_kicker(text: str) -> str:
    """Mono uppercase schedule / reminder label above the card body title."""
    return f"""
    <p style="margin:0 0 12px 0; font-family:{FONT_MONO}; font-size:10px; font-weight:500; letter-spacing:0.14em; text-transform:uppercase; color:{INK_MUTED};">
      {escape(text)}
    </p>
    """


def paper_title(text: str, *, warning: bool = False) -> str:
    color = WARN_INK if warning else INK
    return f"""
    <h1 style="margin:0; font-family:{FONT_SERIF}; font-size:22px; font-weight:400; line-height:1.25; color:{color};">
      {escape(text)}
    </h1>
    """


def paper_body(text: str) -> str:
    """Body copy under a paper title. ``text`` may include safe HTML."""
    return f"""
    <p style="margin:12px 0 0 0; font-family:{FONT_SANS}; font-size:14.5px; line-height:1.7; color:{INK_SOFT};">
      {text}
    </p>
    """


def paper_hint(text: str) -> str:
    return f"""
    <p style="margin:14px 0 0 0; font-family:{FONT_SANS}; font-size:13.5px; line-height:1.65; color:{INK_HINT};">
      {text}
    </p>
    """


def render_email(
    *,
    title: str,
    body_html: str,
    preheader: str = "",
    eyebrow: str = "Orderly Affairs",
    warning: bool = False,
) -> str:
    """Wrap inner HTML in the branded paper / ink email shell (fluid max-width)."""
    brand_mark = email_brand_mark(box=36, img=28)
    safe_title = escape(title)
    safe_eyebrow = escape(eyebrow)
    safe_preheader = escape(preheader or title)
    portal = escape(portal_url())
    support = escape(getattr(settings, "EMAIL_SENDER", "support@orderly-affairs.com"))
    card_border = WARN_BORDER if warning else LINE

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_title}</title>
  <style type="text/css">
    @media only screen and (max-width: 620px) {{
      .oa-shell {{ padding:14px 10px !important; }}
      .oa-card {{ border-radius:12px !important; }}
      .oa-pad {{ padding:20px 18px !important; }}
      .oa-title {{ font-size:20px !important; }}
      .oa-stack-cta {{ display:table !important; }}
      .oa-logo-box {{ width:30px !important; height:30px !important; border-radius:7px !important; }}
      .oa-logo-img {{ width:22px !important; height:22px !important; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:{PAPER};">
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">
    {safe_preheader}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{PAPER};">
    <tr>
      <td align="center" class="oa-shell" style="padding:24px 14px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="oa-card" style="width:100%; max-width:560px; background-color:{BRAND_CARD}; border:1px solid {card_border}; border-radius:12px; overflow:hidden;">
          <tr>
            <td class="oa-pad" style="padding:18px 22px 8px 22px; border-bottom:1px solid {LINE_SOFT};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle" width="48" style="padding-right:12px;">
                    {brand_mark}
                  </td>
                  <td valign="middle">
                    <p style="margin:0; font-family:{FONT_MONO}; font-size:10px; letter-spacing:0.14em; text-transform:uppercase; color:{INK_MUTED}; font-weight:500;">
                      {safe_eyebrow}
                    </p>
                    <p class="oa-title" style="margin:4px 0 0 0; font-family:{FONT_SERIF}; font-size:18px; line-height:1.25; color:{INK}; font-weight:400;">
                      {safe_title}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="oa-pad" style="padding:22px 26px 8px 26px;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td class="oa-pad" style="padding:8px 26px 24px 26px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="border-top:1px solid {LINE}; padding-top:16px;">
                    <p style="margin:0; font-family:{FONT_SANS}; font-size:12px; line-height:1.6; color:{INK_MUTED};">
                      Sent by Orderly Affairs ·
                      <a href="{portal}" style="color:{INK}; text-decoration:none; font-weight:500;">Open portal</a>
                      ·
                      <a href="mailto:{support}" style="color:{INK}; text-decoration:none; font-weight:500;">{support}</a>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_reminder_card(
    *,
    title: str,
    body_html: str,
    preheader: str = "",
    schedule_label: str = "",
    warning: bool = False,
) -> str:
    """Reminder emails: schedule kicker + serif title card (matches design comps).

    Uses fluid max-width (no fixed 390/600 mock widths).
    """
    brand_mark = email_brand_mark(box=28, img=22)
    safe_preheader = escape(preheader or title)
    portal = escape(portal_url())
    support = escape(getattr(settings, "EMAIL_SENDER", "support@orderly-affairs.com"))
    card_border = WARN_BORDER if warning else LINE
    kicker = schedule_kicker(schedule_label) if schedule_label else ""
    heading = paper_title(title, warning=warning)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(title)}</title>
  <style type="text/css">
    @media only screen and (max-width: 620px) {{
      .oa-shell {{ padding:14px 10px !important; }}
      .oa-pad {{ padding:20px 18px !important; }}
      .oa-title {{ font-size:20px !important; }}
      .oa-hide-desk {{ display:none !important; max-height:0 !important; overflow:hidden !important; }}
      .oa-show-mob {{ display:table !important; width:100% !important; }}
      .oa-logo-box {{ width:26px !important; height:26px !important; border-radius:7px !important; }}
      .oa-logo-img {{ width:18px !important; height:18px !important; }}
    }}
    @media only screen and (min-width: 621px) {{
      .oa-show-mob {{ display:none !important; max-height:0 !important; overflow:hidden !important; mso-hide:all; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:{PAPER};">
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">{safe_preheader}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{PAPER};">
    <tr>
      <td align="center" class="oa-shell" style="padding:20px 14px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:560px;">
          <tr>
            <td style="padding:0 0 14px 0;" align="left">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle" style="padding-right:10px;">
                    {brand_mark}
                  </td>
                  <td valign="middle" style="font-family:{FONT_MONO}; font-size:10px; font-weight:500; letter-spacing:0.14em; text-transform:uppercase; color:{INK_MUTED};">
                    Orderly Affairs
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:0 0 10px 0;">{kicker}</td>
          </tr>
          <tr>
            <td class="oa-pad" style="background:#ffffff; border:1px solid {card_border}; border-radius:12px; padding:26px 28px; font-size:15px; color:{INK};">
              <div class="oa-title">{heading}</div>
              {body_html}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 4px 0 4px;">
              <p style="margin:0; font-family:{FONT_SANS}; font-size:12px; line-height:1.6; color:{INK_MUTED}; text-align:center;">
                <a href="{portal}" style="color:{INK}; text-decoration:none; font-weight:500;">Open portal</a>
                ·
                <a href="mailto:{support}" style="color:{INK}; text-decoration:none; font-weight:500;">{support}</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_simple_email(
    *,
    title: str,
    paragraphs: Iterable[str],
    preheader: str = "",
    cta_url: str | None = None,
    cta_label: str | None = None,
    details: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    callout_html: str | None = None,
    greeting_name: str | None = None,
) -> str:
    """Convenience builder for common transactional emails."""
    parts: list[str] = [greeting(greeting_name)]
    for para in paragraphs:
        parts.append(p(para))
    if details:
        if isinstance(details, Mapping):
            rows = list(details.items())
        else:
            rows = list(details)
        parts.append(email_info_rows(rows))
    if callout_html:
        parts.append(callout_html)
    if cta_url and cta_label:
        parts.append(
            f"""
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:18px 0 0 0; width:100%;">
              <tr><td>{email_pill_button(cta_url, cta_label, variant="primary")}</td></tr>
            </table>
            """
        )
    return render_email(
        title=title,
        preheader=preheader or title,
        body_html="".join(parts),
    )
