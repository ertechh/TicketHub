import os
import shutil
import sqlite3
from app import app, db
from sqlalchemy import inspect

print("=" * 50)
print("🔧 FIXING DATABASE ISSUE")
print("=" * 50)

# 1. Kill any running Flask processes (for Windows)
print("\n📌 Step 1: Stopping any running Flask servers...")
os.system('taskkill /f /im python.exe 2>nul')
print("✅ Done")

# 2. Delete ALL database files everywhere
print("\n📌 Step 2: Deleting all database files...")
db_paths = [
    'tickets.db',
    'tickets.db-journal',
    'instance/tickets.db',
    'instance/tickets.db-journal',
    'instance/__pycache__'
]

for path in db_paths:
    if os.path.exists(path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            print(f"  ✅ Deleted: {path}")
        except Exception as e:
            print(f"  ⚠️ Could not delete {path}: {e}")

# 3. Delete instance folder entirely
print("\n📌 Step 3: Removing instance folder...")
if os.path.exists('instance'):
    try:
        shutil.rmtree('instance')
        print("  ✅ Deleted instance folder")
    except Exception as e:
        print(f"  ⚠️ Could not delete instance: {e}")

# 4. Delete __pycache__
print("\n📌 Step 4: Removing Python cache...")
if os.path.exists('__pycache__'):
    try:
        shutil.rmtree('__pycache__')
        print("  ✅ Deleted __pycache__")
    except Exception as e:
        print(f"  ⚠️ Could not delete __pycache__: {e}")

# 5. Create fresh database
print("\n📌 Step 5: Creating fresh database with correct schema...")
with app.app_context():
    db.create_all()
    print("  ✅ Database created!")

# 6. Verify the schema
print("\n📌 Step 6: Verifying schema...")
with app.app_context():
    inspector = inspect(db.engine)
    columns = inspector.get_columns('user')
    print("\n  ✅ User table columns:")
    for col in columns:
        print(f"     - {col['name']} ({col['type']})")
    
    # Check if stripe_account_id exists
    has_stripe = any(col['name'] == 'stripe_account_id' for col in columns)
    if has_stripe:
        print("\n  🎉 SUCCESS! stripe_account_id column exists!")
    else:
        print("\n  ❌ ERROR: stripe_account_id column is STILL missing!")
        print("     This means your app.py User model is missing it.")

print("\n" + "=" * 50)
print("✅ FIX COMPLETE!")
print("=" * 50)
print("\nNow run: python app.py")
print("Then register a new user and test.")