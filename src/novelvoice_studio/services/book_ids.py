"""书籍数字 ID 分配。"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from novelvoice_studio.models.system import BookIdSequence


def allocate_book_id(session: Session) -> str:
    """分配一个永久递增的 ``book_N`` ID。"""

    sequence = session.scalar(select(BookIdSequence).where(BookIdSequence.id == 1))
    if sequence is None:
        sequence = BookIdSequence(id=1, next_value=1)
        session.add(sequence)
        session.flush()

    next_value = session.execute(
        update(BookIdSequence)
        .where(BookIdSequence.id == 1)
        .values(next_value=BookIdSequence.next_value + 1)
        .returning(BookIdSequence.next_value)
    ).scalar_one()
    return f"book_{int(next_value) - 1}"
