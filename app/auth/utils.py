import sendgrid
from sendgrid.helpers.mail import Mail
from random import randint
from datetime import datetime, timedelta
from app.database import otp_collection
from app.config import nextkin_login_url, settings
from app.notifications.email_layout import (
    email_callout,
    email_code_box,
    escape,
    p,
    render_email,
    render_simple_email,
)


# ============================================================
# ✅ Generate + Send OTP (shared for owner)
# ============================================================
async def generate_and_send_otp(email: str):
    otp = randint(100000, 999999)
    expiry = datetime.utcnow() + timedelta(minutes=10)

    await otp_collection.delete_many({"email": email})
    await otp_collection.insert_one({"email": email, "otp": otp, "expires": expiry})

    sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject="Your Orderly Affairs verification code",
        html_content=render_email(
            title="Verification code",
            preheader=f"Your verification code is {otp}",
            body_html="".join(
                [
                    p("Hello,"),
                    p(
                        "Use the verification code below to continue. It expires "
                        "in <b>10 minutes</b>."
                    ),
                    email_code_box(otp),
                    email_callout(
                        "If you did not request this code, you can safely ignore "
                        "this email.",
                        tone="info",
                    ),
                ]
            ),
        ),
    )
    try:
        sg.send(message)
    except Exception as e:
        print(f"SendGrid error (OTP): {e}")


# ============================================================
# ✅ Verify OTP
# ============================================================
async def verify_otp(email: str, otp_input: int) -> bool:
    record = await otp_collection.find_one({"email": email})
    if not record:
        return False
    if datetime.utcnow() > record["expires"]:
        await otp_collection.delete_many({"email": email})
        return False
    return record["otp"] == otp_input


# ============================================================
# ✅ Send Next-of-Kin Credentials
# ============================================================
async def send_nextkin_credentials(
    email: str,
    owner_name: str,
    password: str,
    full_name: str | None = None,
):
    login = nextkin_login_url()
    sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject="Orderly Affairs - Next-of-Kin Account Created",
        html_content=render_simple_email(
            title="Your Next-of-Kin account is ready",
            greeting_name=full_name,
            paragraphs=[
                f"You have been designated as a Next-of-Kin by "
                f"<b>{escape(owner_name)}</b>.",
                "Use the login details below to access the kit when authorized.",
            ],
            details=[
                ("Email", email),
                ("Password", password),
            ],
            cta_url=login,
            cta_label="Log in to Orderly Affairs",
            callout_html=email_callout(
                "Keep these credentials private. The kit owner may also share a "
                "Password Card with additional instructions.",
                tone="info",
            ),
            preheader="Your Next-of-Kin account credentials",
        ),
    )
    try:
        sg.send(message)
    except Exception as e:
        print(f"SendGrid error (NextKin): {e}")
