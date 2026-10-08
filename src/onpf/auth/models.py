from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    invite_id: str | None
    batch_id: str | None
