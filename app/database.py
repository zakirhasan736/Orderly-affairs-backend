from motor.motor_asyncio import AsyncIOMotorClient
import certifi
from app.config import settings

# Secure certificate bundle
ca = certifi.where()

# Async MongoDB client with TLS
client = AsyncIOMotorClient(
    settings.MONGO_URL,
    tls=True,
    tlsCAFile=ca,
    tlsAllowInvalidCertificates=False,
    serverSelectionTimeoutMS=10000
)

db = client["orderly_affairs"]

# ✅ Define all collections you use
users_collection = db["users"]
otp_collection = db["otp"] 
kits_collection = db["kits"]  
letters_collection = db["letters"] 
section_data_collection = db["sections"] 
messageofnextkin_collection = db["nexrkinmessage"] 

print("✅ MongoDB connected successfully")
