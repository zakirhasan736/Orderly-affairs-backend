"""Shared modern HTML email layout for Orderly Affairs.

All transactional emails should wrap content with ``render_email`` so branding
stays consistent: navy header with logo + title, polished body card, footer.
"""

from __future__ import annotations

import html
from typing import Iterable, Mapping, Sequence

from app.config import settings

BRAND_NAVY = "#10213f"
BRAND_NAVY_SOFT = "#1a335f"
BRAND_MUTED = "#64748b"
BRAND_BORDER = "#e2e8f0"
BRAND_BG = "#f4f6f9"
BRAND_CARD = "#ffffff"
BRAND_ACCENT = "#2563eb"


def brand_logo_url() -> str:
    """Public logo URL used in email headers (must be absolute for clients)."""
    custom = (getattr(settings, "EMAIL_LOGO_URL", None) or "").strip()
    if custom:
        return custom
    base = (settings.FRONTEND_URL or "https://portal.orderly-affairs.com").rstrip("/")
    return f"{base}/images/brand-logo.png"


def portal_url() -> str:
    return (settings.FRONTEND_URL or "https://portal.orderly-affairs.com").rstrip("/")


def escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def email_button(url: str, label: str) -> str:
    safe_url = escape(url)
    safe_label = escape(label)
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:24px 0 8px 0;">
      <tr>
        <td align="left" style="border-radius:10px; background-color:{BRAND_NAVY};">
          <a href="{safe_url}"
             style="display:inline-block; padding:14px 22px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:14px; font-weight:700; color:#ffffff; text-decoration:none; border-radius:10px; letter-spacing:0.01em;">
            {safe_label}
          </a>
        </td>
      </tr>
    </table>
    """


def email_code_box(code: str | int) -> str:
    safe = escape(code)
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
      <tr>
        <td align="center" style="background:{BRAND_BG}; border:1px solid {BRAND_BORDER}; border-radius:14px; padding:22px 16px;">
          <p style="margin:0 0 8px 0; font-size:12px; font-weight:600; letter-spacing:0.12em; text-transform:uppercase; color:{BRAND_MUTED}; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
            Verification code
          </p>
          <p style="margin:0; font-size:32px; font-weight:800; letter-spacing:0.28em; color:{BRAND_NAVY}; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">
            {safe}
          </p>
        </td>
      </tr>
    </table>
    """


def email_info_rows(rows: Sequence[tuple[str, str]]) -> str:
    """Render a compact labeled detail list."""
    items = []
    for label, value in rows:
        items.append(
            f"""
            <tr>
              <td style="padding:10px 0; border-bottom:1px solid {BRAND_BORDER}; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
                <p style="margin:0 0 4px 0; font-size:11px; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:{BRAND_MUTED};">{escape(label)}</p>
                <p style="margin:0; font-size:15px; font-weight:600; color:{BRAND_NAVY}; line-height:1.5;">{escape(value)}</p>
              </td>
            </tr>
            """
        )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 4px 0;">
      {''.join(items)}
    </table>
    """


def email_callout(text: str, *, tone: str = "info") -> str:
    tones = {
        "info": ("#eff6ff", "#bfdbfe", BRAND_NAVY),
        "warning": ("#fffbeb", "#fde68a", "#92400e"),
        "danger": ("#fef2f2", "#fecaca", "#991b1b"),
        "success": ("#ecfdf5", "#a7f3d0", "#065f46"),
    }
    bg, border, color = tones.get(tone, tones["info"])
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0;">
      <tr>
        <td style="background:{bg}; border:1px solid {border}; border-radius:12px; padding:14px 16px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:14px; line-height:1.6; color:{color};">
          {text}
        </td>
      </tr>
    </table>
    """


def p(text: str) -> str:
    """Paragraph with standard body styling. ``text`` may include safe HTML."""
    return f"""
    <p style="margin:0 0 14px 0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:15px; line-height:1.7; color:#334155;">
      {text}
    </p>
    """


def greeting(name: str | None = None) -> str:
    label = escape(name) if name and str(name).strip() else "there"
    return p(f"Hello {label},")


def render_email(
    *,
    title: str,
    body_html: str,
    preheader: str = "",
    eyebrow: str = "Orderly Affairs",
) -> str:
    """Wrap inner HTML in the branded Orderly Affairs email shell."""
    logo = escape(brand_logo_url())
    safe_title = escape(title)
    safe_eyebrow = escape(eyebrow)
    safe_preheader = escape(preheader or title)
    portal = escape(portal_url())
    support = escape(getattr(settings, "EMAIL_SENDER", "support@orderly-affairs.com"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_title}</title>
</head>
<body style="margin:0; padding:0; background-color:{BRAND_BG};">
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">
    {safe_preheader}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{BRAND_BG}; padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px; width:100%; background-color:{BRAND_CARD}; border-radius:16px; overflow:hidden; box-shadow:0 10px 30px rgba(16,33,63,0.08);">
          <tr>
            <td style="background:linear-gradient(135deg, {BRAND_NAVY} 0%, {BRAND_NAVY_SOFT} 100%); background-color:{BRAND_NAVY}; padding:22px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle" width="48" style="padding-right:14px;">
                    <img src="{logo}" width="40" height="40" alt="Orderly Affairs" style="display:block; width:40px; height:40px; border-radius:10px; background:#ffffff; object-fit:contain;" />
                  </td>
                  <td valign="middle">
                    <p style="margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:11px; letter-spacing:0.14em; text-transform:uppercase; color:rgba(255,255,255,0.72); font-weight:600;">
                      {safe_eyebrow}
                    </p>
                    <p style="margin:4px 0 0 0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:20px; line-height:1.3; color:#ffffff; font-weight:700;">
                      {safe_title}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 28px 8px 28px;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 28px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="border-top:1px solid {BRAND_BORDER}; padding-top:18px;">
                    <p style="margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:12px; line-height:1.6; color:{BRAND_MUTED};">
                      This message was sent by Orderly Affairs.
                      Visit <a href="{portal}" style="color:{BRAND_NAVY}; text-decoration:none; font-weight:600;">{portal}</a>
                      or contact <a href="mailto:{support}" style="color:{BRAND_NAVY}; text-decoration:none; font-weight:600;">{support}</a>.
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        <p style="margin:16px 0 0 0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:11px; color:#94a3b8; text-align:center;">
          Secure estate organization · Orderly Affairs
        </p>
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
        parts.append(email_button(cta_url, cta_label))
    return render_email(
        title=title,
        preheader=preheader or title,
        body_html="".join(parts),
    )
