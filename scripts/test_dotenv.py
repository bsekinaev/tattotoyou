import os
from pathlib import Path

from dotenv import load_dotenv

# Ищем .env
current = Path(__file__).resolve()
for parent in [current] + list(current.parents):
    if (parent / "pyproject.toml").exists():
        project_root = parent
        break

env_file = project_root / ".env"
print(f"📁 .env location: {env_file}")
print(f"📁 File exists: {env_file.exists()}")
print(f"📁 File size: {env_file.stat().st_size if env_file.exists() else 0} bytes")

# Загружаем
loaded = load_dotenv(dotenv_path=env_file)
print(f"\n✅ load_dotenv() returned: {loaded}")

# Проверяем os.environ
print("\n🔎 Переменные в os.environ после load_dotenv:")
for key in ["TELEGRAM_BOT_TOKEN", "GIGACHAT_CLIENT_ID", "POSTGRES_PASSWORD", "SECRET_KEY"]:
    value = os.environ.get(key)
    if value:
        print(f"  ✅ {key} = {value[:10]}...")
    else:
        print(f"  ❌ {key} = MISSING")

# Проверяем, не BOM ли в начале
if env_file.exists():
    with open(env_file, "rb") as f:
        first_bytes = f.read(10)
        print(f"\n🔬 Первые байты файла: {first_bytes!r}")
        if first_bytes.startswith(b"\xef\xbb\xbf"):
            print("  ⚠️  ОБНАРУЖЕН UTF-8 BOM! Нужно пересохранить файл без BOM.")
        else:
            print("  ✅ BOM не обнаружен")