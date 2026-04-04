# app/routers/__init__.py

from .users import router as users_router
from .wips import router as wips_router
from .projects import router as projects_router
# 추후 작성될 다른 라우터들도 여기에 추가합니다.
# from .inventory import router as inventory_router
from .lantek import router as lantek_router
from .scenarios import router as scenario_router
from .scheduler import router as scheduler_router
from .scenario_cart import router as scenario_cart_router
from .scenario_send import router as scenario_send_router
