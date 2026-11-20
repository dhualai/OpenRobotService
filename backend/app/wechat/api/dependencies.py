from fastapi import Depends
from typing import Optional
from app.modules.admin.utils_das.config import security, DEBUG_MODE

admin_auth = Depends(security if not DEBUG_MODE else lambda: None)