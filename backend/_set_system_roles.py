"""One-time script: set 开发者/超级管理员/用户 as system roles."""
import sys
sys.path.insert(0, '.')

from app.core.db import SessionLocal
from app.models import Role

TARGET_NAMES = ['开发者', '超级管理员', '用户']

def main():
    db = SessionLocal()
    try:
        roles = db.query(Role).filter(Role.name.in_(TARGET_NAMES)).all()
        found_names = {r.name for r in roles}
        updated = 0
        for role in roles:
            if role.role_type != 'system':
                role.role_type = 'system'
                updated += 1
                print(f"  {role.name}: {role.role_type} -> system")
        db.commit()
        print(f"Updated {updated} role(s).")
        for name in TARGET_NAMES:
            if name not in found_names:
                print(f"  WARNING: role '{name}' not found in database")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()

if __name__ == '__main__':
    main()
