"""Fill missing business coordinates; retain complete existing coordinates."""
import json
from app.core.providers import get_c_store
from data_foundation.geography import enrich_coordinates

if __name__ == '__main__':
    print(json.dumps(enrich_coordinates(get_c_store()),ensure_ascii=False,indent=2))
