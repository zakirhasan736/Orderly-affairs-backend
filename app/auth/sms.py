from twilio.rest import Client
from app.config import settings

client = Client(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN
)

def send_sms(to: str, message: str):
    try:
        if not to.startswith("+"):
            raise ValueError("Phone must be in E.164 format")

        msg = client.messages.create(
            body=message,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to,
        )

        print(f"✅ SMS sent successfully → SID: {msg.sid}")

    except Exception as e:
        print(f"❌ SMS sending failed: {str(e)}")
        raise RuntimeError("SMS delivery failed")