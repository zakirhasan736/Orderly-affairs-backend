"""Owner alerts for living NOK grants and death reports."""

from __future__ import annotations

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


def _revoke_button_html() -> str:
    from app.notifications.email_layout import access_url, email_pill_button

    return f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 0 0; width:100%;">
          <tr>
            <td>
              {email_pill_button(access_url(), "Revoke Access", variant="danger", full_width=True)}
            </td>
          </tr>
        </table>
        """


async def notify_owner_access_released(*, owner: dict) -> None:
    """Owner just confirmed living next-of-kin access."""
    try:
        from app.notifications.email_layout import (
            access_url,
            paper_body,
            render_reminder_card,
        )

        html = render_reminder_card(
            schedule_label="Event-driven · access released",
            title="You have released access to your next of kin.",
            preheader="If this is not what you intended, revoke their access immediately.",
            warning=True,
            body_html="".join(
                [
                    paper_body(
                        "If this is not what you intended, you can revoke their "
                        "access immediately."
                    ),
                    _revoke_button_html(),
                ]
            ),
        )
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(
            Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=owner["email"],
                subject="Orderly Affairs – Next-of-Kin Access Alert",
                html_content=html,
            )
        )
        try:
            from app.notifications.push_bridge import notify_web_push

            await notify_web_push(
                owner,
                title="Next-of-Kin access released",
                body=(
                    "You have released access to your next of kin. "
                    "If this is not what you intended, revoke their access immediately."
                ),
                tag="nok-access-released",
                url=access_url(),
                urgency="high",
            )
        except Exception as push_exc:
            print("⚠️ NOK access-released web push failed:", push_exc)
    except Exception as exc:
        print("Owner access-released notification failed:", exc)


async def notify_owner_nextkin_signed_in(*, owner: dict, nextkin: dict) -> None:
    """First completed NOK login after access is live."""
    try:
        from app.notifications.email_layout import (
            FONT_MONO,
            INK_MUTED,
            kit_url,
            paper_body,
            render_reminder_card,
        )

        nk_label = nextkin.get("full_name") or nextkin.get("email") or "Your next of kin"
        html = render_reminder_card(
            schedule_label="Event-driven · someone signed in",
            title=f"{nk_label} signed in to the next-of-kin portal.",
            preheader="Your next of kin opened their Orderly Affairs portal",
            warning=False,
            body_html="".join(
                [
                    f'<p style="margin:0; font-family:{FONT_MONO}; font-size:10px; '
                    f'letter-spacing:0.12em; text-transform:uppercase; color:{INK_MUTED};">'
                    "Living portal login</p>",
                    paper_body(
                        "If this was not expected, open Access Management and "
                        "revoke their access."
                    ),
                    _revoke_button_html(),
                ]
            ),
        )
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(
            Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=owner["email"],
                subject="Orderly Affairs – Next-of-Kin signed in",
                html_content=html,
            )
        )
        try:
            from app.notifications.push_bridge import notify_web_push

            await notify_web_push(
                owner,
                title="Next-of-Kin signed in",
                body=f"{nk_label} signed in to the next-of-kin portal.",
                tag="nok-login",
                url=kit_url(),
                urgency="high",
            )
        except Exception as push_exc:
            print("⚠️ NOK login web push failed:", push_exc)
    except Exception as exc:
        print("Owner login notification failed:", exc)


async def notify_owner_death_report(*, owner: dict, reporter_name: str) -> None:
    """Someone reported a passing. Vault stays sealed until admin release."""
    try:
        from app.notifications.email_layout import (
            access_url,
            email_cta_row,
            escape,
            kit_url,
            paper_body,
            render_reminder_card,
        )

        who = (reporter_name or "Your next of kin").strip()
        html = render_reminder_card(
            schedule_label="Event-driven · passing report",
            title="A passing was reported on your kit.",
            preheader="If you are well, sign in now. Vault access is not released yet.",
            warning=True,
            body_html="".join(
                [
                    paper_body(
                        f"{escape(who)} reported that you have passed. "
                        "Vault access and sealed letters are still locked. "
                        "If you are alive, sign in now — that cancels this report. "
                        "If you do nothing, Orderly Affairs continues verification "
                        "and an admin must still release access before anyone "
                        "can open the vault."
                    ),
                    email_cta_row((kit_url(), "I am alive — sign in to cancel")),
                ]
            ),
        )
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(
            Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=owner["email"],
                subject="Orderly Affairs – Passing report received",
                html_content=html,
            )
        )
        try:
            from app.notifications.push_bridge import notify_web_push

            await notify_web_push(
                owner,
                title="Passing report received",
                body=(
                    "A next of kin reported a passing. Vault access is not "
                    "released yet. Sign in if this is a mistake."
                ),
                tag="nok-death-report",
                url=access_url(),
                urgency="high",
            )
        except Exception as push_exc:
            print("⚠️ Death-report web push failed:", push_exc)
    except Exception as exc:
        print("Owner death-report notification failed:", exc)


async def notify_owner_revoke_succeeded(*, owner: dict) -> None:
    try:
        from app.notifications.email_layout import kit_url, paper_body, render_reminder_card

        html = render_reminder_card(
            schedule_label="Event-driven · access revoked",
            title="Next-of-kin access was revoked.",
            preheader="They cannot open the next-of-kin portal.",
            warning=False,
            body_html="".join(
                [
                    paper_body(
                        "Their access is cut off immediately. They were emailed "
                        "if they had already been notified. You can release "
                        "access again later from Access Management."
                    ),
                ]
            ),
        )
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(
            Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=owner["email"],
                subject="Orderly Affairs – Next-of-kin access revoked",
                html_content=html,
            )
        )
        try:
            from app.notifications.push_bridge import notify_web_push

            await notify_web_push(
                owner,
                title="Next-of-kin access revoked",
                body="Their access is cut off immediately.",
                tag="nok-access-revoked",
                url=kit_url(),
                urgency="high",
            )
        except Exception as push_exc:
            print("⚠️ Owner revoke web push failed:", push_exc)
    except Exception as exc:
        print("Owner revoke confirmation failed:", exc)


async def notify_owner_certificate_wait(
    *,
    owner: dict,
    reporter_name: str,
    wait_ends_at,
    reminder: bool,
    remaining_days: int,
    reminder_day: int | None = None,
) -> None:
    """In-account email + push: 7-day wait after a death certificate is filed."""
    try:
        from app.notifications.email_layout import (
            email_cta_row,
            escape,
            kit_url,
            paper_body,
            render_reminder_card,
        )

        who = (reporter_name or "Someone you named").strip()
        if reminder:
            title = "Reminder: a death certificate is still under review"
            preheader = (
                f"Day {reminder_day or ''} of 7. Sign in if you are well. "
                "The vault is still sealed."
            )
            body = (
                f"This is a scheduled reminder (every 2 days). {escape(who)} "
                "submitted a death certificate so we can review after-death access. "
                f"About {remaining_days} day"
                f"{'' if remaining_days == 1 else 's'} remain in the 7-day wait. "
                "Sign in now if you are alive — that cancels the request. "
                "We will not unlock your Vault during this wait."
            )
            subject = "Orderly Affairs – Reminder: death certificate under review"
        else:
            title = "A death certificate was submitted for review"
            preheader = "7-day wait started. Sign in if you are well. The vault is still sealed."
            body = (
                f"{escape(who)} asked for after-death access and uploaded a death "
                "certificate. We posted this note on your account and started a "
                "7-day wait. Reminders go out every 2 days. Sign in if you are "
                "alive — that cancels the request immediately. Access stays sealed "
                "until this window ends and our team still reviews the file."
            )
            subject = "Orderly Affairs – Death certificate submitted for review"
        html = render_reminder_card(
            schedule_label=(
                "Reminder · every 2 days"
                if reminder
                else "Event-driven · 7-day wait"
            ),
            title=title,
            preheader=preheader,
            warning=True,
            body_html="".join(
                [
                    paper_body(body),
                    email_cta_row((kit_url(), "I am alive — sign in to cancel")),
                ]
            ),
        )
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(
            Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=owner["email"],
                subject=subject,
                html_content=html,
            )
        )
        try:
            from app.notifications.push_bridge import notify_web_push

            await notify_web_push(
                owner,
                title=title,
                body=preheader,
                tag="nok-death-certificate-wait",
                url=kit_url(),
                urgency="high",
            )
        except Exception as push_exc:
            print("⚠️ Certificate-wait web push failed:", push_exc)
    except Exception as exc:
        print("Owner certificate-wait notification failed:", exc)


async def notify_owner_death_report_cancelled(*, owner: dict) -> None:
    try:
        from app.notifications.email_layout import kit_url, paper_body, render_reminder_card

        html = render_reminder_card(
            schedule_label="Event-driven · you signed in",
            title="The passing report was cancelled.",
            preheader="You signed in, so this kit stays locked to others.",
            warning=False,
            body_html="".join(
                [
                    paper_body(
                        "Because you signed in, Orderly Affairs treated the "
                        "passing report as a false trigger. Vault access and "
                        "sealed letters stay closed."
                    ),
                ]
            ),
        )
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(
            Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=owner["email"],
                subject="Orderly Affairs – Passing report cancelled",
                html_content=html,
            )
        )
        try:
            from app.notifications.push_bridge import notify_web_push

            await notify_web_push(
                owner,
                title="Passing report cancelled",
                body="You signed in, so the passing report was cancelled.",
                tag="nok-death-report-cancelled",
                url=kit_url(),
                urgency="normal",
            )
        except Exception:
            pass
    except Exception as exc:
        print("Owner death-report cancel notice failed:", exc)

