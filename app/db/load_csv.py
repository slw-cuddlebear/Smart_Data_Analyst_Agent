"""把 data/sample.csv 导入 SQLite 的 sales 表"""

import pandas as pd
from sqlalchemy import text

from app.config import BASE_DIR
from app.db.engine import get_engine, list_tables

CSV_FILE = BASE_DIR / "data" / "sample.csv"
TABLE_NAME = "sales"


def main() -> None:
    if not CSV_FILE.exists():
        raise SystemExit(f"找不到文件: {CSV_FILE}，请先运行 data/generate_sample.py")

    df = pd.read_csv(CSV_FILE)
    print(f"读取 CSV: {len(df)} 行, {len(df.columns)} 列")
    print(f"列名: {list(df.columns)}")

    engine = get_engine()

    # 覆盖写入
    df.to_sql(TABLE_NAME, engine, if_exists="replace", index=False)
    print(f"已写入表: {TABLE_NAME}")

    # 验证
    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar()
        print(f"表内行数: {count}")

        top = conn.execute(
            text(f"SELECT region, SUM(amount) AS total FROM {TABLE_NAME} "
                 f"GROUP BY region ORDER BY total DESC LIMIT 5")
        ).fetchall()
        print("\n各地区销售额 Top5：")
        for row in top:
            print(f"  {row[0]:<6} {row[1]:>12,.2f}")

    print(f"\n当前数据库所有表: {list_tables()}")


if __name__ == "__main__":
    main()