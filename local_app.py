#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local web console for NewAPI check-in configuration and execution."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from checkin import NewAPICheckin, parse_accounts

try:
    from dingtalk_notifier import send_checkin_notification
except ImportError:
    send_checkin_notification = None


ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "local_web"
CONFIG_FILE = ROOT_DIR / "local_config.json"
LEGACY_ACCOUNTS_FILE = ROOT_DIR / "newapi_accounts.json"
SCHEDULE_STATE_FILE = ROOT_DIR / "local_schedule_state.json"
CHECKIN_LOCK = threading.Lock()
SCHEDULER_STOP = threading.Event()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_config() -> Dict[str, Any]:
    return {
        "accounts": [],
        "dingtalk": {
            "webhook": "",
            "secret": "",
            "notify_after_checkin": False,
        },
        "schedule": {
            "enabled": False,
            "time": "08:10",
            "run_missed": False,
        },
    }


def normalize_account(raw: Dict[str, Any], index: int) -> Dict[str, str]:
    account_id = str(raw.get("id") or f"account-{index + 1}")
    name = str(raw.get("name") or f"账号 {index + 1}").strip()
    url = str(raw.get("url") or "").strip()
    session = str(raw.get("session") or "").strip()
    user_id = str(raw.get("user_id") or "").strip()
    cf_clearance = str(raw.get("cf_clearance") or "").strip()

    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    return {
        "id": account_id,
        "name": name,
        "url": url,
        "session": session,
        "user_id": user_id,
        "cf_clearance": cf_clearance,
    }


def parse_schedule_time(value: str) -> Tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM format")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("time is out of range")
    return hour, minute


def normalize_schedule(raw: Any) -> Dict[str, Any]:
    schedule = raw if isinstance(raw, dict) else {}
    time_value = str(schedule.get("time") or "08:10").strip()
    try:
        hour, minute = parse_schedule_time(time_value)
        time_value = f"{hour:02d}:{minute:02d}"
    except Exception:
        time_value = "08:10"

    return {
        "enabled": bool(schedule.get("enabled")),
        "time": time_value,
        "run_missed": bool(schedule.get("run_missed", False)),
    }


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    config = default_config()
    accounts = raw.get("accounts", [])
    if isinstance(accounts, list):
        config["accounts"] = [
            normalize_account(item, index)
            for index, item in enumerate(accounts)
            if isinstance(item, dict)
        ]

    dingtalk = raw.get("dingtalk", {})
    if isinstance(dingtalk, dict):
        config["dingtalk"] = {
            "webhook": str(dingtalk.get("webhook") or "").strip(),
            "secret": str(dingtalk.get("secret") or "").strip(),
            "notify_after_checkin": bool(dingtalk.get("notify_after_checkin")),
        }

    config["schedule"] = normalize_schedule(raw.get("schedule", {}))
    return config


def read_json_file(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_config() -> Tuple[Dict[str, Any], str]:
    local_data = read_json_file(CONFIG_FILE)
    if isinstance(local_data, dict):
        return normalize_config(local_data), str(CONFIG_FILE)

    legacy_data = read_json_file(LEGACY_ACCOUNTS_FILE)
    if isinstance(legacy_data, list):
        return normalize_config({"accounts": legacy_data}), str(LEGACY_ACCOUNTS_FILE)
    if isinstance(legacy_data, dict) and isinstance(legacy_data.get("accounts"), list):
        return normalize_config(legacy_data), str(LEGACY_ACCOUNTS_FILE)

    return default_config(), str(CONFIG_FILE)


def validate_accounts(accounts: List[Dict[str, str]], require_session: bool = True) -> List[str]:
    errors: List[str] = []
    for index, account in enumerate(accounts, 1):
        label = account.get("name") or f"账号 {index}"
        url = account.get("url", "")
        session = account.get("session", "")
        if not url:
            errors.append(f"{label}: 站点 URL 不能为空")
        elif not url.startswith(("http://", "https://")):
            errors.append(f"{label}: 站点 URL 必须以 http:// 或 https:// 开头")
        if require_session and not session:
            errors.append(f"{label}: Session Cookie 不能为空")
    return errors


def save_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    config = normalize_config(raw)
    errors = validate_accounts(config["accounts"], require_session=True)
    if errors:
        raise ValueError("\n".join(errors))

    payload = {
        "accounts": config["accounts"],
        "dingtalk": config["dingtalk"],
        "schedule": config["schedule"],
        "updated_at": _now(),
    }
    with CONFIG_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return payload


def load_scheduler_state() -> Dict[str, Any]:
    try:
        data = read_json_file(SCHEDULE_STATE_FILE)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_scheduler_state(state: Dict[str, Any]) -> None:
    payload = dict(state)
    payload["updated_at"] = _now()
    with SCHEDULE_STATE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def next_schedule_time(schedule: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    if not schedule.get("enabled"):
        return None
    now = now or datetime.now()
    hour, minute = parse_schedule_time(schedule.get("time", "08:10"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M:%S")


def schedule_run_key(schedule: Dict[str, Any], now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return f"{now.strftime('%Y-%m-%d')} {schedule.get('time', '08:10')}"


def is_schedule_due(schedule: Dict[str, Any], state: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    if not schedule.get("enabled"):
        return False

    now = now or datetime.now()
    hour, minute = parse_schedule_time(schedule.get("time", "08:10"))
    scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    seconds_after = (now - scheduled_at).total_seconds()

    if schedule.get("run_missed"):
        due = seconds_after >= 0
    else:
        due = 0 <= seconds_after < 60

    return due and state.get("last_run_key") != schedule_run_key(schedule, now)


def scheduler_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = normalize_config(config or load_config()[0])
    schedule = normalized["schedule"]
    state = load_scheduler_state()
    return {
        "enabled": schedule.get("enabled", False),
        "time": schedule.get("time", "08:10"),
        "run_missed": schedule.get("run_missed", False),
        "next_run_at": next_schedule_time(schedule),
        "last_run_at": state.get("last_run_at", ""),
        "last_run_key": state.get("last_run_key", ""),
        "last_message": state.get("last_message", ""),
        "last_summary": state.get("last_summary", {}),
        "running": CHECKIN_LOCK.locked(),
    }


def import_accounts(raw_text: str) -> List[Dict[str, str]]:
    text = raw_text.strip()
    if not text:
        return []

    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            return normalize_config(data)["accounts"]
        if isinstance(data, list):
            return normalize_config({"accounts": data})["accounts"]
    except json.JSONDecodeError:
        pass

    parsed = parse_accounts(text)
    return [normalize_account(item, index) for index, item in enumerate(parsed)]


def export_accounts(accounts: List[Dict[str, str]]) -> Dict[str, str]:
    exportable = []
    for account in accounts:
        item = {
            "url": account.get("url", ""),
            "session": account.get("session", ""),
            "name": account.get("name", ""),
        }
        if account.get("user_id"):
            item["user_id"] = account["user_id"]
        if account.get("cf_clearance"):
            item["cf_clearance"] = account["cf_clearance"]
        exportable.append(item)

    simple = ",".join(
        f"{account.get('url', '')}#{account.get('session', '')}"
        for account in accounts
        if account.get("url") and account.get("session")
    )
    return {
        "json": json.dumps(exportable, ensure_ascii=False, indent=2),
        "simple": simple,
    }


def public_user_info(user_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not user_info:
        return {}
    return {
        "id": user_info.get("id"),
        "username": user_info.get("username") or user_info.get("display_name") or "",
        "quota": user_info.get("quota"),
        "used_quota": user_info.get("used_quota"),
    }


def format_quota(quota: Optional[int]) -> str:
    if quota is None:
        return "-"
    try:
        value = int(quota)
    except (TypeError, ValueError):
        return str(quota)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return str(value)


def account_client(account: Dict[str, str]) -> NewAPICheckin:
    return NewAPICheckin(
        account["url"],
        account["session"],
        account.get("user_id") or None,
        account.get("cf_clearance") or None,
    )


def test_single_account(raw_account: Dict[str, Any]) -> Dict[str, Any]:
    account = normalize_account(raw_account, 0)
    errors = validate_accounts([account], require_session=True)
    if errors:
        return {"success": False, "message": "\n".join(errors)}

    try:
        client = account_client(account)
        user_info = client.get_user_info()
        if not user_info:
            return {
                "success": False,
                "message": "无法获取用户信息，Session 可能无效或站点不可达",
            }
        return {
            "success": True,
            "message": "连接成功",
            "user": public_user_info(user_info),
            "user_id": str(user_info.get("id") or client.user_id or ""),
        }
    except Exception as exc:
        return {"success": False, "message": f"测试失败: {exc}"}


def _run_checkins(config: Dict[str, Any], notify: bool = False) -> Dict[str, Any]:
    normalized = normalize_config(config)
    accounts = normalized["accounts"]
    errors = validate_accounts(accounts, require_session=True)
    if not accounts:
        errors.append("请先添加至少一个账号")
    if errors:
        return {"success": False, "message": "\n".join(errors), "results": []}

    started_at = _now()
    results: List[Dict[str, Any]] = []
    notify_results: List[Dict[str, Any]] = []

    for index, account in enumerate(accounts, 1):
        item: Dict[str, Any] = {
            "name": account.get("name") or f"账号 {index}",
            "url": NewAPICheckin._mask_url(account["url"]),
            "success": False,
            "message": "",
            "quota_awarded": None,
            "quota_text": "-",
            "checkin_count": None,
            "user": {},
        }

        try:
            client = account_client(account)
            user_info = client.get_user_info()
            item["user"] = public_user_info(user_info)

            result = client.checkin()
            item["success"] = bool(result.get("success"))
            item["message"] = result.get("message") or ("签到成功" if item["success"] else "签到失败")
            item["checkin_date"] = result.get("checkin_date")
            item["quota_awarded"] = result.get("quota_awarded")
            item["quota_text"] = format_quota(result.get("quota_awarded"))

            if item["success"]:
                history = client.get_checkin_history()
                stats = history.get("stats") if isinstance(history, dict) else None
                if stats:
                    item["checkin_count"] = stats.get("checkin_count")
                    item["total_quota"] = stats.get("total_quota")
                    item["total_quota_text"] = format_quota(stats.get("total_quota"))
        except Exception as exc:
            item["success"] = False
            item["message"] = f"执行失败: {exc}"

        notify_result = {
            "name": item["name"],
            "success": item["success"],
            "message": item["message"],
            "quota_awarded": item.get("quota_awarded"),
            "checkin_count": item.get("checkin_count"),
            "session_expired": "session" in item.get("message", "").lower()
            or "认证" in item.get("message", ""),
        }
        notify_results.append(notify_result)
        results.append(item)

    success_count = len([item for item in results if item["success"]])
    fail_count = len(results) - success_count
    notification_sent = False
    notification_message = ""

    if notify and normalized["dingtalk"].get("webhook"):
        os.environ["DINGTALK_WEBHOOK"] = normalized["dingtalk"]["webhook"]
        if normalized["dingtalk"].get("secret"):
            os.environ["DINGTALK_SECRET"] = normalized["dingtalk"]["secret"]
        elif "DINGTALK_SECRET" in os.environ:
            del os.environ["DINGTALK_SECRET"]

        if send_checkin_notification:
            notification_sent = bool(send_checkin_notification(notify_results, started_at))
            notification_message = "钉钉通知已发送" if notification_sent else "钉钉通知发送失败"
        else:
            notification_message = "钉钉通知模块不可用"

    return {
        "success": fail_count == 0,
        "message": f"完成: 成功 {success_count}, 失败 {fail_count}",
        "started_at": started_at,
        "finished_at": _now(),
        "summary": {
            "total": len(results),
            "success": success_count,
            "failed": fail_count,
        },
        "notification_sent": notification_sent,
        "notification_message": notification_message,
        "results": results,
    }


def run_checkins(config: Dict[str, Any], notify: bool = False) -> Dict[str, Any]:
    if not CHECKIN_LOCK.acquire(blocking=False):
        return {
            "success": False,
            "message": "已有签到任务正在运行",
            "summary": {"total": 0, "success": 0, "failed": 0},
            "results": [],
        }

    try:
        return _run_checkins(config, notify=notify)
    finally:
        CHECKIN_LOCK.release()


def run_scheduled_checkin(config: Dict[str, Any], run_key: str) -> None:
    state = load_scheduler_state()
    state.update({
        "last_run_key": run_key,
        "last_run_at": _now(),
        "last_message": "定时签到运行中",
        "last_summary": {},
    })
    save_scheduler_state(state)

    notify = bool(config.get("dingtalk", {}).get("notify_after_checkin"))
    result = run_checkins(config, notify=notify)
    state = load_scheduler_state()
    state.update({
        "last_run_key": run_key,
        "last_run_at": result.get("started_at") or _now(),
        "last_message": result.get("message", ""),
        "last_summary": result.get("summary", {}),
    })
    save_scheduler_state(state)


def scheduler_loop() -> None:
    while not SCHEDULER_STOP.wait(20):
        try:
            config, _ = load_config()
            schedule = config["schedule"]
            state = load_scheduler_state()
            now = datetime.now()
            if is_schedule_due(schedule, state, now):
                run_scheduled_checkin(config, schedule_run_key(schedule, now))
        except Exception as exc:
            state = load_scheduler_state()
            state.update({
                "last_message": f"定时器异常: {exc}",
                "last_error_at": _now(),
            })
            save_scheduler_state(state)
            time.sleep(5)


def start_scheduler() -> threading.Thread:
    thread = threading.Thread(target=scheduler_loop, name="newapi-local-scheduler", daemon=True)
    thread.start()
    return thread


class LocalAppHandler(SimpleHTTPRequestHandler):
    server_version = "NewAPILocalApp/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (_now(), fmt % args))

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        if not body.strip():
            return {}
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _serve_file(self, request_path: str) -> None:
        clean_path = unquote(urlparse(request_path).path)
        if clean_path == "/":
            clean_path = "/index.html"
        target = (WEB_DIR / clean_path.lstrip("/")).resolve()
        web_root = WEB_DIR.resolve()
        if web_root not in target.parents and target != web_root:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.guess_type(str(target)))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path.startswith("/api/scheduler/status"):
            self._send_json(scheduler_status())
            return

        if self.path.startswith("/api/config"):
            config, source = load_config()
            self._send_json(
                {
                    "config": config,
                    "source": source,
                    "config_path": str(CONFIG_FILE),
                    "exports": export_accounts(config["accounts"]),
                    "scheduler": scheduler_status(config),
                }
            )
            return

        self._serve_file(self.path)

    def do_POST(self) -> None:
        try:
            payload = self._read_payload()

            if self.path == "/api/config":
                config = save_config(payload.get("config", payload))
                self._send_json(
                    {
                        "success": True,
                        "message": "配置已保存",
                        "config": normalize_config(config),
                        "config_path": str(CONFIG_FILE),
                        "exports": export_accounts(config["accounts"]),
                        "scheduler": scheduler_status(config),
                    }
                )
                return

            if self.path == "/api/import":
                accounts = import_accounts(str(payload.get("text") or ""))
                self._send_json(
                    {
                        "success": bool(accounts),
                        "accounts": accounts,
                        "message": f"已导入 {len(accounts)} 个账号" if accounts else "没有识别到账号配置",
                    }
                )
                return

            if self.path == "/api/account/test":
                result = test_single_account(payload.get("account", {}))
                self._send_json(result, HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST)
                return

            if self.path == "/api/checkin":
                config = payload.get("config") or load_config()[0]
                save_before_run = bool(payload.get("save_before_run", True))
                if save_before_run:
                    config = save_config(config)
                notify = bool(payload.get("notify"))
                result = run_checkins(config, notify=notify)
                self._send_json(result, HTTPStatus.OK if result.get("results") else HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"success": False, "message": "未知接口"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"success": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)
        except json.JSONDecodeError:
            self._send_json({"success": False, "message": "JSON 格式错误"}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"success": False, "message": f"服务异常: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def open_browser_later(url: str) -> None:
    timer = threading.Timer(0.6, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the NewAPI local web console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not WEB_DIR.exists():
        raise SystemExit(f"Missing UI directory: {WEB_DIR}")

    server = None
    selected_port = args.port
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer((args.host, port), LocalAppHandler)
            selected_port = port
            break
        except OSError:
            continue

    if server is None:
        raise SystemExit(f"No available local port found from {args.port} to {args.port + 19}")

    url = f"http://{args.host}:{selected_port}/"
    print("=" * 60)
    print("NewAPI local console is running")
    print(f"Open: {url}")
    print(f"Config file: {CONFIG_FILE}")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    if not args.no_browser:
        open_browser_later(url)

    start_scheduler()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local console...")
    finally:
        SCHEDULER_STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
