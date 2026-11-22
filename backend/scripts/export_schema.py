import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app.models import Base

dialect = mysql.dialect()
lines = [
    "CREATE DATABASE IF NOT EXISTS harness CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
    "USE harness;",
    "",
]
for table in Base.metadata.sorted_tables:
    lines.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
    lines.append("")
path = Path(__file__).resolve().parents[2] / "sql" / "init.sql"
path.parent.mkdir(exist_ok=True)
path.write_text("\n".join(lines), encoding="utf-8")
print(f"tables={len(Base.metadata.tables)} bytes={path.stat().st_size}")
