"""学生已修课程的轻量持久化档案。"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from core.schema import CompletedCourse

# 档案标识由使用者自己设置。允许单字符标识，避免演示时填入 ``1`` 之类的
# 新标识被接口拒绝；不同标识天然对应不同的、初始为空的档案。
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ProfileStore:
    """SQLite 档案：只存稳定用户标识和课程状态，不保留原始提问。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS student_profiles (
                profile_id TEXT PRIMARY KEY,
                courses_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def valid_profile_id(profile_id: str | None) -> bool:
        return bool(profile_id and _PROFILE_ID_RE.fullmatch(profile_id))

    def get_courses(self, profile_id: str | None) -> list[CompletedCourse]:
        if not self.valid_profile_id(profile_id):
            return []
        with self._connect() as connection:
            row = connection.execute(
                "SELECT courses_json FROM student_profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            return []
        try:
            return [CompletedCourse.model_validate(item) for item in json.loads(row["courses_json"])]
        except (TypeError, ValueError):
            return []

    def merge_courses(self, profile_id: str | None, incoming: list[CompletedCourse]) -> list[CompletedCourse]:
        """把本次明确提供的课程并入档案，同课程以本次状态为准。"""
        existing = self.get_courses(profile_id)
        merged = {item.code.upper(): item.model_copy(update={"code": item.code.upper()}) for item in existing}
        for item in incoming:
            merged[item.code.upper()] = item.model_copy(update={"code": item.code.upper()})
        result = list(merged.values())
        if not self.valid_profile_id(profile_id) or not incoming:
            return result
        self._save(profile_id, result)
        return result

    def replace_courses(self, profile_id: str | None, courses: list[CompletedCourse]) -> list[CompletedCourse]:
        if not self.valid_profile_id(profile_id):
            return []
        normalized = {item.code.upper(): item.model_copy(update={"code": item.code.upper()}) for item in courses}
        result = list(normalized.values())
        self._save(profile_id, result)
        return result

    def remove_course(self, profile_id: str | None, course_code: str) -> list[CompletedCourse]:
        remaining = [item for item in self.get_courses(profile_id) if item.code.upper() != course_code.upper()]
        if self.valid_profile_id(profile_id):
            self._save(profile_id, remaining)
        return remaining

    def clear(self, profile_id: str | None) -> None:
        if not self.valid_profile_id(profile_id):
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM student_profiles WHERE profile_id = ?", (profile_id,))

    def _save(self, profile_id: str, courses: list[CompletedCourse]) -> None:
        encoded = json.dumps([item.model_dump(mode="json") for item in courses], ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO student_profiles (profile_id, courses_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(profile_id) DO UPDATE SET
                courses_json = excluded.courses_json, updated_at = CURRENT_TIMESTAMP""",
                (profile_id, encoded),
            )
