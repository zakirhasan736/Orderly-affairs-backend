import sendgrid
from sendgrid.helpers.mail import Mail
from random import randint
from datetime import datetime, timedelta
from app.database import otp_collection
from app.config import nextkin_login_url, settings


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
        html_content=f"""
        <div style='font-family:Arial,sans-serif'>
          <p>Hello,</p>
          <p>Your verification code is:</p>
          <h2 style='letter-spacing:2px'>{otp}</h2>
          <p>This code will expire in 10 minutes.</p>
        </div>
        """,
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
    sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject="Orderly Affairs - Next-of-Kin Account Created",
        html_content=f"""
        <div style='font-family:Arial,sans-serif'>
          <p>Hello {full_name or ''},</p>
          <p>You have been designated as a Next-of-Kin by {owner_name}.</p>
          <p><b>Login credentials:</b></p>
          <p>Email: {email}<br>Password: {password}</p>
          <p>Log in here: 
             <a href="{nextkin_login_url()}">
             {nextkin_login_url()}</a></p>
        </div>
        """,
    )
    try:
        sg.send(message)
    except Exception as e:
        print(f"SendGrid error (NextKin): {e}")
