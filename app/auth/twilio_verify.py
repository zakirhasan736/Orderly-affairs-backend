from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from app.config import settings

client = Client(
    settings.TWILIO_ACCOUNT_SID,
    settings.TWILIO_AUTH_TOKEN,
)


def _dev_log(*args) -> None:
    if settings.APP_ENV == "development":
        print(*args)


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

        _dev_log("Twilio Verify sent", verification.status)
        return verification

    except TwilioRestException as e:
        _dev_log("Twilio Verify send failed", e.code, e.status)
        raise RuntimeError("OTP delivery failed")

    except Exception:
        _dev_log("Twilio Verify send failed (unknown)")
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

        _dev_log("Twilio Verify checked", result.status)
        return result

    except TwilioRestException as e:
        _dev_log("Twilio Verify check failed", e.code, e.status)
        raise RuntimeError("OTP verification failed")

    except Exception:
        _dev_log("Twilio Verify check failed (unknown)")
        raise RuntimeError("OTP verification failed")
