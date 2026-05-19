from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from app.config import settings

client = Client(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN,
)

def send_verification_code(to: str):
    if not to or not to.startswith("+"):
        raise ValueError("Phone must be in E.164 format")

    try:
        verification = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(
            to=to,
            channel="sms",
        )

        print("✅ Twilio Verify sent")
        print("SID:", verification.sid)
        print("TO:", verification.to)
        print("STATUS:", verification.status)

        return verification

    except TwilioRestException as e:
        print("❌ Twilio Verify send failed")
        print("Status:", e.status)
        print("Code:", e.code)
        print("Message:", e.msg)
        raise RuntimeError(f"OTP delivery failed: {e.msg}")

    except Exception as e:
        print(f"❌ Verify send failed: {str(e)}")
        raise RuntimeError("OTP delivery failed")


def check_verification_code(to: str, code: str):
    if not to or not to.startswith("+"):
        raise ValueError("Phone must be in E.164 format")

    try:
        result = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(
            to=to,
            code=str(code),
        )

        print("✅ Twilio Verify checked")
        print("TO:", result.to)
        print("STATUS:", result.status)

        return result

    except TwilioRestException as e:
        print("❌ Twilio Verify check failed")
        print("Status:", e.status)
        print("Code:", e.code)
        print("Message:", e.msg)
        raise RuntimeError(f"OTP verification failed: {e.msg}")

    except Exception as e:
        print(f"❌ Verify check failed: {str(e)}")
        raise RuntimeError("OTP verification failed")