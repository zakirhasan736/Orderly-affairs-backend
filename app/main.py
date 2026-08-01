from dotenv import load_dotenv
load_dotenv()
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.uploads.routes import router as upload_router
from fastapi.middleware.cors import CORSMiddleware
from app.auth.routes import router as auth_router

from app.auth.inactivity_scheduler import start_owner_inactivity_scheduler
from app.billing.trial_scheduler import start_trial_scheduler
from app.kits.routes_core import router as kit_router
from app.letters.routes import router as letters_router
from app.letters.scheduler import start_scheduler as start_nok_letter_scheduler
from app.notifications.section_expiry_scheduler import (
    start_section_expiry_scheduler,
)
from app.sections.section_meta_routes import router as section_meta_router
from app.billing.routes import billing_router
from app.nexrkinmessage.routes import router as message_of_nextkin_letters_router
from app.nexrkinmessage.scheduler import check_scheduled_letters
from app.billing.webhooks import webhook_router
from app.admin.billing import admin_billing_router
from app.support.routes import support_router, admin_support_router
from app.feedback.routes import feedback_router, admin_feedback_router
from app.sections.section1_vital_information.router import (
    router as section1_router
)
from app.onboarding.routes import router as onboarding_router
from app.sections.section5_vehicles.router import router as section5_router
from app.sections.section6_main_residence.router import router as section6_router
from app.sections.section7_insurance_policies.router import (
    router as section7_router,
)
from app.sections.section8_community_membership.router import (
    router as section8_router,
)
from app.sections.section9_charitable_giving.router import (
    router as section9_router,
)
from app.sections.section10_education_accomplishments.router import (
    router as section10_router,
)
from app.sections.section11_military_service.router import (
    router as section11_router,
)
from app.sections.section12_banking_financial_accounts.router import (
    router as section12_router,
)
from app.sections.section13_passwords_online_accounts.router import (
    router as section13_router,
)
from app.sections.section14_investment_accounts.router import router as section14_router
from app.sections.section15_health_information.router import router as section15_router
from app.sections.section16_credit_cards_debt.router import router as section16_router
from app.sections.section17_family_treasured_connections.router import router as section17_router
from app.sections.section18_employment_business.router import router as section18_router
from app.sections.section19_assets_valuables.router import router as section19_router
from app.sections.section20_legal_document_records.router import router as section20_router
from app.sections.section21_estate_planning_finalwishes.router import router as section21_router

from app.ai.ai_upload_routes import router as ai_upload_router
from app.ai.ai_autofill_routes import router as ai_autofill_router
# ai_brain_routes: admin-only skill export/settings — mount when admin panel exists
from app.security.encrypt_at_rest_migration import run_encryption_migration
from app.security.security_audit import run_security_audit
from app.config import settings
from app.security.https_redirect import HTTPSRedirectMiddleware
from app.security.security_headers import SecurityHeadersMiddleware
from app.security.api_rate_limit import VaultApiRateLimitMiddleware
from app.security.error_handlers import (
    http_exception_handler,
    starlette_http_exception_handler,
    validation_exception_handler,
    unhandled_exception_handler,
)

app = FastAPI(
    title="Orderly Affairs Backend API",
    docs_url="/docs" if settings.APP_ENV == "development" else None,
    redoc_url="/redoc" if settings.APP_ENV == "development" else None,
    openapi_url="/openapi.json" if settings.APP_ENV == "development" else None,
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(StarletteHTTPException, starlette_http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(VaultApiRateLimitMiddleware)

# Trailing slash mismatch breaks exact-origin CORS checks
_frontend = (settings.FRONTEND_URL or "").rstrip("/")
origins = [o for o in {_frontend, "https://portal.orderly-affairs.com"} if o]
if settings.APP_ENV == "development":
    for local in ("http://localhost:3000", "http://127.0.0.1:3000"):
        if local not in origins:
            origins.append(local)

# CORS must be outermost so even error/redirect responses get ACAO headers.
# (Starlette: last add_middleware = first to handle the request.)
app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.on_event("startup")
async def startup():
    from app.auth.otp_security import ensure_otp_send_lock_index
    from app.auth.phone import ensure_owner_phone_index
    from app.database import users_collection

    await ensure_otp_send_lock_index()
    try:
        await ensure_owner_phone_index(users_collection)
    except Exception as exc:
        if settings.APP_ENV == "development":
            print("Owner phone unique index warning:", exc)

    # 1️⃣ Start APScheduler-based NOK LETTERS
    start_nok_letter_scheduler()

     # 2️⃣ Start Trial reminder scheduler
    start_trial_scheduler()

    # 3️⃣ Owner inactivity check (90 days + 15 day follow-up)
    start_owner_inactivity_scheduler()

    # 4️⃣ Expiry / renewal reminders for ALL sections (10 → 5 → 1 → 0 days)
    start_section_expiry_scheduler()

    # 5️⃣ Semi-annual kit review (“keep it current”)
    from app.notifications.kit_review_emails import start_kit_review_scheduler

    start_kit_review_scheduler()
    
    # 2️⃣ Start simple async loop for messages
    async def message_scheduler_loop():
        while True:
            try:
                await check_scheduled_letters()
            except Exception as e:
                if settings.APP_ENV == "development":
                    print("NOK message scheduler error:", e)

            await asyncio.sleep(60)

    asyncio.create_task(message_scheduler_loop())

    async def encryption_migration_loop():
        try:
            await run_encryption_migration()
            await run_security_audit()
        except Exception as exc:
            if settings.APP_ENV == "development":
                print("Encryption-at-rest migration error:", exc)

    asyncio.create_task(encryption_migration_loop())

    if settings.APP_ENV == "development":
        print("Both letter & message schedulers started")

app.include_router(upload_router)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(webhook_router)
app.include_router(admin_billing_router)
app.include_router(support_router)
app.include_router(admin_support_router)
app.include_router(feedback_router)
app.include_router(admin_feedback_router)

app.include_router(kit_router)
app.include_router(letters_router)
app.include_router(section_meta_router)
app.include_router(section1_router)
app.include_router(section5_router)
app.include_router(section6_router)
app.include_router(section7_router)
app.include_router(section8_router)
app.include_router(section9_router)
app.include_router(section10_router)
app.include_router(section11_router)
app.include_router(section12_router)
app.include_router(section13_router)
app.include_router(section14_router)
app.include_router(section15_router)
app.include_router(section16_router)
app.include_router(section17_router)
app.include_router(section18_router)
app.include_router(section19_router)
app.include_router(section20_router)
app.include_router(section21_router)
app.include_router(message_of_nextkin_letters_router)
app.include_router(onboarding_router)

app.include_router(ai_upload_router)
app.include_router(ai_autofill_router)
# app.include_router(ai_brain_router)  # admin panel — not public

@app.get("/")
def health_check():
    return {"status": "ok"}
