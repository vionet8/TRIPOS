# -*- coding: utf-8 -*-
"""
storage.py
Redis（Upstash）/ ファイル のどちらでも動くストレージ抽象レイヤー

環境変数が設定されていればRedis、なければローカルのJSONファイルを使う。
"""
import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_redis = None
_redis_ok = None  # Noneは未確認、True/Falseは確認済み
_DATA_DIR = Path(__file__).parent / "data"


def _get_redis():
    global _redis, _redis_ok
    if _redis_ok is False:
        return None  # 一度失敗したら再試行しない
    if _redis is not None:
        return _redis
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        _redis_ok = False
        logger.info("Upstash 環境変数なし → ファイルストレージを使用")
        return None
    try:
        from upstash_redis import Redis
        r = Redis(url=url, token=token)
        # 接続テスト
        r.ping()
        _redis = r
        _redis_ok = True
        logger.info("✅ Upstash Redis に接続しました")
    except Exception as e:
        _redis_ok = False
        logger.warning(f"Redis 接続失敗（ファイルにフォールバック）: {e}")
    return _redis


def load(key: str) -> dict:
    """キーに対応する辞書を返す。存在しなければ空辞書。"""
    r = _get_redis()
    if r is not None:
        try:
            val = r.get(key)
            if val is None:
                return {}
            if isinstance(val, str):
                return json.loads(val)
            if isinstance(val, dict):
                return val
            return {}
        except Exception as e:
            logger.error(f"Redis load失敗 key={key}: {e}")
            # Redis失敗時はファイルフォールバックへ（fall through）

    # ---- ファイルフォールバック（ローカル開発 / Redis失敗時）----
    _DATA_DIR.mkdir(exist_ok=True)
    path = _DATA_DIR / f"{key}.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save(key: str, data: dict):
    """辞書をキーに保存する。"""
    r = _get_redis()
    if r is not None:
        try:
            r.set(key, json.dumps(data, ensure_ascii=False))
            return
        except Exception as e:
            logger.error(f"Redis save失敗 key={key}: {e}")
            # Redis失敗時はファイルフォールバックへ（fall through）

    # ---- ファイルフォールバック ----
    _DATA_DIR.mkdir(exist_ok=True)
    path = _DATA_DIR / f"{key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def user_key(user_id: str, key: str) -> str:
    """ユーザーごとの名前空間キーを生成"""
    return f"{key}_{user_id}"


def load_for_user(user_id: str, key: str) -> dict:
    return load(user_key(user_id, key))


def save_for_user(user_id: str, key: str, data: dict):
    save(user_key(user_id, key), data)


def ping() -> str:
    """Redis/ファイル接続状況を返す（デバッグ用）"""
    r = _get_redis()
    if r is not None:
        return f"Redis OK: {os.environ.get('UPSTASH_REDIS_REST_URL', '')}"
    return f"File storage: {_DATA_DIR}"
