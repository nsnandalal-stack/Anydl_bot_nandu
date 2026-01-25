 import os
import sys

# Check environment variables before anything else
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")

print("=" * 50)
print("Checking Environment Variables...")
print(f"API_ID: {'✓ Set' if API_ID else '✗ MISSING'}")
print(f"API_HASH: {'✓ Set' if API_HASH else '✗ MISSING'}")
print(f"SESSION_STRING: {'✓ Set' if SESSION_STRING else '✗ MISSING'}")
print("=" * 50)

# Stop if variables are missing
missing = []
if not API_ID:
    missing.append("API_ID")
if not API_HASH:
    missing.append("API_HASH")
if not SESSION_STRING:
    missing.append("SESSION_STRING")

if missing:
    print(f"❌ ERROR: Missing environment variables: {', '.join(missing)}")
    print("Please set them in Koyeb dashboard:")
    print("Service → Settings → Environment Variables")
    sys.exit(1)

# Convert API_ID to integer
try:
    API_ID = int(API_ID)
except ValueError:
    print(f"❌ ERROR: API_ID must be a number, got: {API_ID}")
    sys.exit(1)

print("✓ All environment variables are set correctly!")
