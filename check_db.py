from app import app, db
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print(f"Tables in database: {tables}")
    
    if 'user' in tables:
        columns = inspector.get_columns('user')
        print("\nColumns in 'user' table:")
        for col in columns:
            print(f"  - {col['name']} ({col['type']})")
    else:
        print("\n❌ 'user' table doesn't exist yet!")
        print("   This means you need to register a user first.")