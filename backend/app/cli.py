"""商业版运维 CLI：初始管理员创建 / 口令重置 / 管理员列表。

用法（在 backend 目录执行，读取 backend/.env）：

    python -m app.cli create-admin --username admin
    python -m app.cli reset-password --username admin
    python -m app.cli list-admins

口令优先从 ``--password`` 读取（仅建议本地使用），其次读取环境变量
``BOOTSTRAP_ADMIN_PASSWORD``，否则交互式输入两次。口令经强度校验后只存
argon2 哈希；所有操作写入审计日志。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.core.constants import Role
from app.core.security import hash_password, validate_password_strength
from app.infra.db import get_session_factory
from app.models.task_repo import User
from app.repositories.audit_repo import AuditRepo
from app.repositories.user_repo import UserRepo


def _read_password(args: argparse.Namespace) -> str:
    """按 --password → 环境变量 → 交互输入 的顺序取口令。"""
    password = args.password or os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if password:
        return password
    first = getpass.getpass("请输入口令（输入不回显）：")
    second = getpass.getpass("请再次输入口令：")
    if first != second:
        raise ValueError("两次输入的口令不一致")
    return first


async def _create_admin(args: argparse.Namespace) -> int:
    password = _read_password(args)
    validate_password_strength(password)
    session = get_session_factory("task_repo")()
    try:
        existing = await UserRepo.get_by_username(session, args.username)
        if existing is not None:
            print(f"创建失败：账号 {args.username} 已存在（role={existing.role}）", file=sys.stderr)
            return 1
        user = await UserRepo.create(
            session,
            username=args.username,
            password_hash=await asyncio.to_thread(hash_password, password),
            role=Role.ADMIN.value,
            display_name=args.display_name,
        )
        await asyncio.to_thread(
            AuditRepo.append,
            session,
            actor="cli",
            action="user.created",
            detail={"user_id": user.id, "username": user.username, "role": Role.ADMIN.value, "source": "cli"},
        )
    finally:
        session.close()
    print(f"已创建管理员：{args.username}（id={user.id}）")
    return 0


async def _reset_password(args: argparse.Namespace) -> int:
    password = _read_password(args)
    validate_password_strength(password)
    session = get_session_factory("task_repo")()
    try:
        user = await UserRepo.update_password(
            session, args.username, await asyncio.to_thread(hash_password, password)
        )
        if user is None:
            print(f"重置失败：账号 {args.username} 不存在", file=sys.stderr)
            return 1
        await asyncio.to_thread(
            AuditRepo.append,
            session,
            actor="cli",
            action="user.password_reset",
            detail={"user_id": user.id, "username": user.username, "source": "cli"},
        )
    finally:
        session.close()
    print(f"已重置口令：{args.username}")
    return 0


async def _list_admins(_args: argparse.Namespace) -> int:
    session = get_session_factory("task_repo")()
    try:
        admins = list(
            session.execute(select(User).where(User.role == Role.ADMIN.value).order_by(User.id)).scalars()
        )
    finally:
        session.close()
    if not admins:
        print("当前没有管理员账号。")
        return 0
    print(f"{'ID':<6}{'账号':<24}{'状态':<8}显示名")
    for user in admins:
        print(f"{user.id:<6}{user.username:<24}{'启用' if user.is_active else '停用':<8}{user.display_name or '-'}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="星枢 StarPivot 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="创建管理员账号")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", default="系统管理员")
    create.add_argument("--password", help="口令（不传则读 BOOTSTRAP_ADMIN_PASSWORD 或交互输入）")
    create.set_defaults(func=_create_admin)

    reset = sub.add_parser("reset-password", help="重置指定账号口令")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password", help="新口令（不传则读 BOOTSTRAP_ADMIN_PASSWORD 或交互输入）")
    reset.set_defaults(func=_reset_password)

    listing = sub.add_parser("list-admins", help="列出管理员账号")
    listing.set_defaults(func=_list_admins)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return int(asyncio.run(args.func(args)))
    except ValueError as exc:
        print(f"参数不合法：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  CLI 顶层兜底，展示简明错误
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
