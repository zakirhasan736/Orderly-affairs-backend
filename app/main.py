from dotenv import load_dotenv
load_dotenv()
import asyncio
from fastapi import FastAPI
from app.uploads.routes import router as upload_router
from fastapi.middleware.cors import CORSMiddleware
from app.auth.routes import router as auth_router
# from app.routes.nextkin_routes import router as nextkin_router
from app.billing.trial_scheduler import start_trial_scheduler
from app.kits.routes_core import router as kit_router
from app.letters.routes import router as letters_router
from app.letters.scheduler import start_scheduler as start_nok_letter_scheduler
from app.billing.routes import billing_router
from app.nexrkinmessage.routes import router as message_of_nextkin_letters_router
from app.nexrkinmessage.scheduler import check_scheduled_letters
from app.billing.webhooks import webhook_router
from app.admin.billing import admin_billing_router
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

app = FastAPI(title="Orderly Affairs Backend API")

origins = [
    "http://localhost:3000",
    "https://portal.orderly-affairs.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.on_event("startup")
async def startup():
    # 1️⃣ Start APScheduler-based NOK LETTERS
    start_nok_letter_scheduler()

     # 2️⃣ Start Trial reminder scheduler
    start_trial_scheduler()
    
    # 2️⃣ Start simple async loop for messages
    async def message_scheduler_loop():
        while True:
            try:
                await check_scheduled_letters()
            except Exception as e:
                print("❌ NOK message scheduler error:", e)

            await asyncio.sleep(60)

    asyncio.create_task(message_scheduler_loop())

    print("✅ Both letter & message schedulers started")

app.include_router(upload_router)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(webhook_router)
app.include_router(admin_billing_router)

# app.include_router(nextkin_router)
app.include_router(kit_router)
app.include_router(letters_router)
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


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Orderly Affairs backend is running."}
for route in app.routes:
    print(route.path)
