import asyncio
import random
import string
from typing import Optional, Union
from enum import Enum
from dataclasses import dataclass, field


CARD_VALUES: list[Union[int, str]] = [0, 1, 2, 3, 5, 8, 13, 100, "?", "☕"]


class Phase(str, Enum):
    JOINING = "joining"
    VOTING = "voting"
    REVEALED = "revealed"


@dataclass
class User:
    id: str
    name: str
    is_admin: bool = False


@dataclass
class Story:
    id: str
    title: str
    description: str = ""
    order: int = 0


@dataclass
class Room:
    id: str
    name: str
    admin_id: str
    phase: Phase = Phase.VOTING
    users: dict[str, User] = field(default_factory=dict)
    stories: list[Story] = field(default_factory=list)
    current_story_idx: int = -1
    votes: dict[str, Union[int, str]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        return self.users.get(user_id)

    def is_admin(self, user_id: str) -> bool:
        user = self.get_user_by_id(user_id)
        return user is not None and user.is_admin

    def current_story(self) -> Optional[Story]:
        if 0 <= self.current_story_idx < len(self.stories):
            return self.stories[self.current_story_idx]
        return None

    def to_public_dict(self) -> dict:
        users_list = []
        for u in self.users.values():
            users_list.append({
                "id": u.id,
                "name": u.name,
                "is_admin": u.is_admin,
                "has_voted": u.id in self.votes,
            })

        stories_list = []
        for s in self.stories:
            stories_list.append({
                "id": s.id,
                "title": s.title,
                "description": s.description,
                "order": s.order,
            })

        cs = self.current_story()
        result = {
            "id": self.id,
            "name": self.name,
            "admin_id": self.admin_id,
            "phase": self.phase.value,
            "users": users_list,
            "stories": stories_list,
            "current_story_idx": self.current_story_idx,
            "current_story": {
                "id": cs.id,
                "title": cs.title,
                "description": cs.description,
                "order": cs.order,
            } if cs else None,
            "votes": {},
        }

        if self.phase == Phase.REVEALED:
            revealed: dict[str, Union[int, str]] = {}
            for uid, val in self.votes.items():
                user = self.users.get(uid)
                if user:
                    revealed[user.name] = val
            result["votes"] = revealed

        return result


def _generate_id(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


class RoomManager:
    def __init__(self):
        self._rooms: dict[str, Room] = {}

    def create_room(self, name: str, admin_name: str) -> tuple[str, str]:
        room_id = self._generate_room_id()
        admin_id = _generate_id()
        admin_user = User(id=admin_id, name=admin_name, is_admin=True)
        room = Room(
            id=room_id,
            name=name,
            admin_id=admin_id,
            users={admin_id: admin_user},
        )
        self._rooms[room_id] = room
        return room_id, admin_id

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def add_user(self, room_id: str, name: str) -> Optional[str]:
        room = self.get_room(room_id)
        if not room:
            return None
        user_id = _generate_id()
        user = User(id=user_id, name=name)
        room.users[user_id] = user
        return user_id

    def remove_user(self, room_id: str, user_id: str):
        room = self.get_room(room_id)
        if room:
            room.users.pop(user_id, None)
            room.votes.pop(user_id, None)

    def _generate_room_id(self) -> str:
        while True:
            code = _generate_id(6)
            if code not in self._rooms:
                return code
