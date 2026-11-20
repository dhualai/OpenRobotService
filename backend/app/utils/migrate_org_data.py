"""一次性数据迁移脚本：从 users 表的 company/department 字符串列
迁移到 companies/departments 主数据表，并回填 users.company_id/department_id。

执行方式：python -m app.utils.migrate_org_data
幂等设计：重复执行不会产生重复数据。
"""
import uuid
import logging
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models.organization import Company, Department

logger = logging.getLogger(__name__)

UNCATEGORIZED_COMPANY_NAME = "未分类"


def run_migration():
    db = SessionLocal()
    try:
        # 1. 创建"未分类"公司（幂等）
        uncategorized = db.query(Company).filter(Company.name == UNCATEGORIZED_COMPANY_NAME).first()
        if not uncategorized:
            uncategorized = Company(
                id=str(uuid.uuid4()),
                name=UNCATEGORIZED_COMPANY_NAME,
                status="approved",
            )
            db.add(uncategorized)
            db.flush()
            logger.info(f"创建「未分类」公司: id={uncategorized.id}")

        # 2. 从 users 表提取所有非空 distinct company 名称
        rows = db.execute(text(
            "SELECT DISTINCT company FROM users "
            "WHERE company IS NOT NULL AND company != ''"
        )).fetchall()
        existing_company_names = {row[0] for row in rows}

        # 加上未分类
        existing_company_names.add(UNCATEGORIZED_COMPANY_NAME)

        # 3. 插入 companies 表（幂等：跳过已存在的）
        company_map = {}  # name → Company 对象
        for name in existing_company_names:
            existing = db.query(Company).filter(Company.name == name).first()
            if existing:
                company_map[name] = existing
            else:
                comp = Company(
                    id=str(uuid.uuid4()),
                    name=name,
                    status="approved",
                )
                db.add(comp)
                db.flush()
                company_map[name] = comp
                logger.info(f"创建公司: {name} → id={comp.id}")

        # 4. 从 users 表提取所有非空 (company, department) 组合
        dept_rows = db.execute(text(
            "SELECT DISTINCT company, department FROM users "
            "WHERE department IS NOT NULL AND department != ''"
        )).fetchall()

        dept_map = {}  # (company_name, dept_name) → Department 对象
        for row in dept_rows:
            company_name = row[0] if row[0] else None
            dept_name = row[1]

            # 确定公司：有公司名用对应公司，否则归到"未分类"
            if company_name and company_name in company_map:
                comp = company_map[company_name]
            else:
                comp = uncategorized

            # 幂等检查
            existing_dept = db.query(Department).filter(
                Department.name == dept_name,
                Department.company_id == comp.id,
            ).first()
            if existing_dept:
                dept_map[(company_name, dept_name)] = existing_dept
            else:
                dept = Department(
                    id=str(uuid.uuid4()),
                    name=dept_name,
                    company_id=comp.id,
                    status="approved",
                )
                db.add(dept)
                db.flush()
                dept_map[(company_name, dept_name)] = dept
                logger.info(f"创建部门: {dept_name} (公司: {comp.name}) → id={dept.id}")

        # 5. 回填 users.company_id
        users_with_company = db.execute(text(
            "SELECT id, company FROM users "
            "WHERE company IS NOT NULL AND company != '' AND company_id IS NULL"
        )).fetchall()
        for row in users_with_company:
            user_id = row[0]
            company_name = row[1]
            comp = company_map.get(company_name)
            if comp:
                db.execute(text(
                    "UPDATE users SET company_id = :cid WHERE id = :uid"
                ), {"cid": comp.id, "uid": user_id})
        logger.info(f"回填 company_id: {len(users_with_company)} 条")

        # 6. 回填 users.department_id
        users_with_dept = db.execute(text(
            "SELECT id, company, department FROM users "
            "WHERE department IS NOT NULL AND department != '' AND department_id IS NULL"
        )).fetchall()
        for row in users_with_dept:
            user_id = row[0]
            company_name = row[1] if row[1] else None
            dept_name = row[2]
            dept = dept_map.get((company_name, dept_name))
            if not dept:
                # 可能 company_name 为空但 department 在"未分类"下
                dept = dept_map.get((None, dept_name))
            if dept:
                db.execute(text(
                    "UPDATE users SET department_id = :did WHERE id = :uid"
                ), {"did": dept.id, "uid": user_id})
        logger.info(f"回填 department_id: {len(users_with_dept)} 条")

        db.commit()
        logger.info("迁移完成")

    except Exception as e:
        db.rollback()
        logger.error(f"迁移失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
