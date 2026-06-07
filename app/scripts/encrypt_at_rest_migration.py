import asyncio

from dotenv import load_dotenv

load_dotenv()

from app.security.encrypt_at_rest_migration import run_encryption_migration


if __name__ == "__main__":
    results = asyncio.run(run_encryption_migration())
    print(results)
