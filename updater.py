#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VoxCPM2 自動更新模組
- 開啟 App 時自動檢查（可被使用者關閉）
- 每 15 天自動確認一次
- 手動「檢查更新」
- 安全更新：先下載到暫存，全部成功才覆蓋；保護使用者資料
所有對外行為都包了 try/except，網路問題絕不讓主程式崩潰。
"""

import os
import json
import shutil
import datetime
import tempfile
import urllib.request

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
CHECK_RECORD_FILE = os.path.join(APP_DIR, "last_update_check.json")

# 你的 GitHub raw 路徑（更新來源）
RAW_BASE = "https://raw.githubusercontent.com/kerich1n/VoxCPM2-App/main"

# 每隔幾天自動確認一次
CHECK_INTERVAL_DAYS = 15

# 更新時要抓的程式檔（使用者資料如 license.key / settings.json / data 不在此列，故不會被覆蓋）
FILES_TO_UPDATE = [
    "voxapp.py",
    "updater.py",
    "voxcpm_icon.png",
    "version.txt",
]

# 更新時「絕對不能動」的使用者資料（保險用的黑名單，避免日後誤加進 FILES_TO_UPDATE）
PROTECTED = {"license.key", "settings.json", "profiles.json"}

# 預設設定
DEFAULT_SETTINGS = {"auto_update_enabled": True}


# ----------------------------------------------------------------------
# 版本號
# ----------------------------------------------------------------------
def get_local_version():
    """讀本機 version.txt；讀不到回傳 0.0.0（會被視為最舊，提示更新）。"""
    path = os.path.join(APP_DIR, "version.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def get_remote_version():
    """抓 GitHub 上的 version.txt；失敗回傳 None（當作不檢查）。"""
    try:
        req = urllib.request.Request(
            f"{RAW_BASE}/version.txt",
            headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.read().decode("utf-8").strip()
    except Exception:
        return None


def _parse(v):
    """把 '1.2.3' 變成 [1,2,3] 方便比較；壞掉的格式回傳 [0]。"""
    try:
        return [int(x) for x in v.strip().split(".")]
    except Exception:
        return [0]


def is_newer(remote, local):
    """remote 是否比 local 新。"""
    if not remote:
        return False
    return _parse(remote) > _parse(local)


# ----------------------------------------------------------------------
# 設定（自動更新開關）— 記在使用者電腦
# ----------------------------------------------------------------------
def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = DEFAULT_SETTINGS.copy()
        merged.update(data)   # 補齊缺漏欄位，相容舊設定檔
        return merged
    except Exception:
        return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def is_auto_update_enabled():
    return load_settings().get("auto_update_enabled", True)


def set_auto_update_enabled(enabled):
    s = load_settings()
    s["auto_update_enabled"] = bool(enabled)
    save_settings(s)
    return s["auto_update_enabled"]


# ----------------------------------------------------------------------
# 15 天自動檢查節流
# ----------------------------------------------------------------------
def _due_for_auto_check():
    """距離上次自動檢查是否已達 CHECK_INTERVAL_DAYS 天。"""
    if not os.path.exists(CHECK_RECORD_FILE):
        return True
    try:
        with open(CHECK_RECORD_FILE, "r", encoding="utf-8") as f:
            last = json.load(f).get("last_check")
        last_date = datetime.date.fromisoformat(last)
        return (datetime.date.today() - last_date).days >= CHECK_INTERVAL_DAYS
    except Exception:
        return True


def _record_auto_check():
    try:
        with open(CHECK_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_check": datetime.date.today().isoformat()}, f)
    except Exception:
        pass


# ----------------------------------------------------------------------
# 檢查更新（回傳結構化結果，不直接彈窗，UI 自己決定怎麼呈現）
# ----------------------------------------------------------------------
def check_for_update():
    """
    回傳 dict：
      {"status": "update", "local": "1.0.1", "remote": "1.1.0"}   有新版
      {"status": "latest", "local": "1.0.1"}                       已最新
      {"status": "offline"}                                        抓不到（沒網路等）
    """
    local = get_local_version()
    remote = get_remote_version()
    if remote is None:
        return {"status": "offline", "local": local}
    if is_newer(remote, local):
        return {"status": "update", "local": local, "remote": remote}
    return {"status": "latest", "local": local}


def auto_check_on_startup():
    """
    開 App 時呼叫。會自己看「開關」和「15 天節流」。
    回傳 check_for_update() 的結果，或 {"status": "skipped"}。
    """
    if not is_auto_update_enabled():
        return {"status": "skipped", "reason": "disabled"}
    if not _due_for_auto_check():
        return {"status": "skipped", "reason": "throttled"}
    result = check_for_update()
    # 不論結果如何，只要真的連線檢查過了就記錄時間（offline 不記，下次還會再試）
    if result.get("status") in ("update", "latest"):
        _record_auto_check()
    return result


# ----------------------------------------------------------------------
# 執行更新：先下載到暫存，全部成功才覆蓋
# ----------------------------------------------------------------------
def _download_one(filename, dest_path):
    """下載單一檔案到 dest_path。成功 True／失敗 False。"""
    try:
        req = urllib.request.Request(
            f"{RAW_BASE}/{filename}",
            headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        # 空檔案視為失敗，避免把好檔覆蓋成空的
        if not data:
            return False
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def perform_update():
    """
    執行更新。回傳 dict：
      {"ok": True,  "updated": [...]}                成功，需提示使用者重開
      {"ok": False, "error": "下載失敗，已保留原版"}  失敗，原版未動
    安全策略：
      1) 全部檔案先下載到暫存資料夾
      2) 任何一個下載失敗 → 整批放棄，原本的檔案完全沒被碰
      3) 全部成功 → 才一個個覆蓋
      4) PROTECTED 內的使用者資料永遠不覆蓋
    """
    targets = [f for f in FILES_TO_UPDATE if f not in PROTECTED]

    tmpdir = tempfile.mkdtemp(prefix="voxupdate_")
    try:
        # 階段一：全部下載到暫存
        staged = {}
        for fn in targets:
            tmp_path = os.path.join(tmpdir, fn)
            if not _download_one(fn, tmp_path):
                return {"ok": False, "error": f"下載 {fn} 失敗，已保留原版，App 可照常使用。"}
            staged[fn] = tmp_path

        # 階段二：全部成功才覆蓋
        updated = []
        for fn, tmp_path in staged.items():
            dest = os.path.join(APP_DIR, fn)
            shutil.copy2(tmp_path, dest)
            updated.append(fn)

        return {"ok": True, "updated": updated}
    except Exception as e:
        return {"ok": False, "error": f"更新過程發生問題，已保留原版：{e}"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
