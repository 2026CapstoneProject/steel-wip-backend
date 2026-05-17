#!/usr/bin/env python3
"""
Database Migration: Add estimated_wip_id column to batch_items table
Purpose: Connect INBOUND BatchItems to EstimatedWips for material spec tracking
"""

import asyncio
import sys
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Add app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings


async def check_column_exists(session: AsyncSession, table: str, column: str) -> bool:
    """Check if a column exists in the database."""
    query = text(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{table}'
        AND TABLE_SCHEMA = '{settings.DB_NAME}'
        AND COLUMN_NAME = '{column}'
    """)
    result = await session.execute(query)
    return result.fetchone() is not None


async def check_foreign_key_exists(session: AsyncSession, constraint_name: str) -> bool:
    """Check if a foreign key constraint exists."""
    query = text(f"""
        SELECT CONSTRAINT_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE CONSTRAINT_NAME = '{constraint_name}'
        AND TABLE_SCHEMA = '{settings.DB_NAME}'
    """)
    result = await session.execute(query)
    return result.fetchone() is not None


async def migrate():
    """Execute the migration."""
    # Create engine and session
    db_url = f"mysql+aiomysql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    engine = create_async_engine(db_url, echo=False)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("\n" + "="*70)
    print("Database Migration: Add estimated_wip_id to batch_items")
    print("="*70 + "\n")

    try:
        async with async_session() as session:
            # 1. Check if column already exists
            print("✓ Step 1: Checking if column exists...")
            column_exists = await check_column_exists(session, 'batch_items', 'estimated_wip_id')

            if column_exists:
                print("  ✅ Column 'estimated_wip_id' already exists. Migration skipped.\n")
                return True

            # 2. Add column
            print("✓ Step 2: Adding 'estimated_wip_id' column...")
            add_column_sql = text("""
                ALTER TABLE batch_items
                ADD COLUMN estimated_wip_id INT DEFAULT NULL
                COMMENT 'INBOUND 시 EstimatedWips 참조'
                AFTER steel_wip_id
            """)
            await session.execute(add_column_sql)
            await session.commit()
            print("  ✅ Column added successfully")

            # 3. Add foreign key constraint
            print("✓ Step 3: Adding foreign key constraint...")
            fk_exists = await check_foreign_key_exists(session, 'batch_items_ibfk_5')
            if not fk_exists:
                add_fk_sql = text("""
                    ALTER TABLE batch_items
                    ADD CONSTRAINT batch_items_ibfk_5
                    FOREIGN KEY (estimated_wip_id)
                    REFERENCES estimated_wips(id)
                    ON DELETE SET NULL
                    ON UPDATE CASCADE
                """)
                await session.execute(add_fk_sql)
                await session.commit()
                print("  ✅ Foreign key constraint added successfully")
            else:
                print("  ℹ️  Foreign key constraint already exists")

            # 4. Add index
            print("✓ Step 4: Adding index for query performance...")
            check_index_sql = text("""
                SHOW INDEXES FROM batch_items WHERE Column_name = 'estimated_wip_id'
            """)
            result = await session.execute(check_index_sql)
            index_exists = result.fetchone() is not None

            if not index_exists:
                add_index_sql = text("""
                    ALTER TABLE batch_items
                    ADD INDEX ix_batch_items_estimated_wip_id (estimated_wip_id)
                """)
                await session.execute(add_index_sql)
                await session.commit()
                print("  ✅ Index added successfully")
            else:
                print("  ℹ️  Index already exists")

            # 5. Verify the migration
            print("✓ Step 5: Verifying migration...")
            verify_sql = text("""
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'batch_items'
                AND TABLE_SCHEMA = :db_name
                AND COLUMN_NAME = 'estimated_wip_id'
            """)
            result = await session.execute(verify_sql, {"db_name": settings.DB_NAME})
            column_info = result.fetchone()

            if column_info:
                col_name, col_type, is_nullable, col_comment = column_info
                print(f"  ✅ Column verified:")
                print(f"     - Name: {col_name}")
                print(f"     - Type: {col_type}")
                print(f"     - Nullable: {is_nullable}")
                print(f"     - Comment: {col_comment}")

            # 6. Check foreign key constraints
            print("✓ Step 6: Verifying foreign key constraints...")
            fk_sql = text("""
                SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_NAME = 'batch_items'
                AND TABLE_SCHEMA = :db_name
                AND COLUMN_NAME = 'estimated_wip_id'
            """)
            result = await session.execute(fk_sql, {"db_name": settings.DB_NAME})
            fk_info = result.fetchone()

            if fk_info:
                constraint, table, col, ref_table, ref_col = fk_info
                print(f"  ✅ Foreign key verified:")
                print(f"     - Constraint: {constraint}")
                print(f"     - References: {ref_table}.{ref_col}")

            # 7. Show summary
            print("\n" + "="*70)
            print("Migration Summary")
            print("="*70)
            print("✅ All migration steps completed successfully!")
            print("\nNext steps:")
            print("  1. Test LANTEK import with the new migration")
            print("  2. Verify INBOUND items are stored with estimated_wip_id")
            print("  3. Update frontend to display EstimatedWips info in INBOUND rows")
            print("="*70 + "\n")

            return True

    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        print("\nTroubleshooting:")
        print("  - Check database credentials in .env file")
        print("  - Verify database is running")
        print("  - Check that estimated_wips table exists")
        print("  - Review the error message above for details")
        return False

    finally:
        await engine.dispose()


if __name__ == "__main__":
    success = asyncio.run(migrate())
    sys.exit(0 if success else 1)
