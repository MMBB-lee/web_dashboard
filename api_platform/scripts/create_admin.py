"""离线创建管理员（03-接口总览 §3：首版用户由管理员离线创建，无公开注册）。

用法（api_platform 目录下）：
    .\.venv\Scripts\python.exe scripts\create_admin.py <username> --display-name <名称>

依赖：C 的 data_foundation 包须已安装（0.1.9 顶层导出 create_store）。
C 0.1.9 没有顶层 create_user()，用户经 uow.auth.create_user 创建，
故这里走 D 的适配层 app.services.c_auth_store.create_user()。
密码经 getpass 在终端输入，不落盘、不写日志。
"""

import argparse
import getpass
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="离线创建管理员账户")
    parser.add_argument("username", help="用户名（3—64 字符）")
    parser.add_argument("--display-name", default=None, help="显示名称")
    args = parser.parse_args()

    if not (3 <= len(args.username) <= 64):
        print("[create_admin] 用户名长度须为 3—64 字符", file=sys.stderr)
        return 1

    password = getpass.getpass("设置管理员密码（至少 8 字符）：")
    if len(password) < 8:
        print("[create_admin] 密码至少 8 字符", file=sys.stderr)
        return 1
    confirm = getpass.getpass("再次输入密码：")
    if password != confirm:
        print("[create_admin] 两次输入不一致", file=sys.stderr)
        return 1

    try:
        from app.services.c_auth_store import create_user
    except ImportError:
        print(
            "[create_admin] 缺少 C 的 data_foundation 包（须顶层导出 create_store()）。\n"
            "请先安装：python -m pip install ..\\..\\03-C的交接包与公告\\...\\anhui_data_foundation-0.1.9-py3-none-any.whl",
            file=sys.stderr,
        )
        return 1

    from app.core.security import PasswordService

    user = create_user(
        username=args.username,
        password_hash=PasswordService.hash_password(password),
        display_name=args.display_name or args.username,
        role="admin",
    )
    print(f"[create_admin] 已创建管理员：{user['username']}（id={user['id']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
