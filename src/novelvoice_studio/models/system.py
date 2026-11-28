"""系统级单例与计数器。"""

from __future__ import annotations

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base


class BookIdSequence(Base):
    """书籍数字 ID 的持久递增计数器。

    使用独立计数器而不是 MAX(id)，确保彻底删除书籍后编号仍不复用。
    """

    __tablename__ = "book_id_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
