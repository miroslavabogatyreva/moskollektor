"""Заявки. Задача MOS-43 (Q4.6), приёмка М-16.

Пока не сделан блок Q6, у заявки нет ни модели данных, ни таблицы с нужными
колонками (maint.notification получит forecast_id и due_at только в Q6.1) —
тикет прямым текстом требует, чтобы оба метода отвечали пустым списком и 200,
а не выдумывали форму ответа, которую Q6 потом не подтвердит.
"""
from fastapi import APIRouter, Depends

from app.auth.deps import require

router = APIRouter(prefix="/api")


@router.get("/orders")
async def list_orders(_user=Depends(require("orders.read"))):
    return []


@router.get("/orders/{order_id}")
async def get_order(order_id: int, _user=Depends(require("orders.read"))):
    # ponytail: форма ответа временная — список, а не объект карточки заявки,
    # как того требует тикет MOS-43 буквально. Q6.5 заменит [] на объект
    # {forecast_id, due_at, ...}; клиент, написанный сегодня под массив,
    # на этой замене сломается осознанно, а не тихо.
    return []
