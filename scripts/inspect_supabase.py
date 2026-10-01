"""Read-only inspection of the existing tables and Storage bucket."""
import json
from app.core.config import get_settings
from app.services.supabase import inspect_schema

if __name__ == "__main__":
    print(json.dumps(inspect_schema(get_settings()), indent=2, default=str))
