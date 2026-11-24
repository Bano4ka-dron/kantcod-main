#!/usr/bin/env python
"""Диагностический тест GigaChat по данным из .env.

Запуск:
    python scripts/test_gigachat.py
    python scripts/test_gigachat.py --no-chat     # только OAuth
    python scripts/test_gigachat.py --prompt "Привет"

Переменные окружения:
    GIGACHAT_CLIENT_ID      (обязательно)
    GIGACHAT_CLIENT_SECRET  (обязательно)
    GIGACHAT_SCOPE          по умолчанию GIGACHAT_API_PERS
    GIGACHAT_MODEL          по умолчанию GigaChat
    GIGACHAT_OAUTH_URL      по умолчанию https://ngw.devices.sberbank.ru:9443/api/v2/oauth
    GIGACHAT_API_URL        по умолчанию https://gigachat.devices.sberbank.ru/api/v1/chat/completions
    GIGACHAT_VERIFY_SSL     true/false, по умолчанию true
    GIGACHAT_CA_BUNDLE      путь к PEM с корневыми сертификатами Минцифры
    GIGACHAT_TIMEOUT        по умолчанию 30 (секунды)

Секреты не печатаются: токен маскируется, client_secret не выводится.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import uuid

import requests


class EnvError(RuntimeError):
    """Не задана обязательная переменная окружения."""


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def get_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvError(f"Не задана переменная {name}")
    return value


def get_verify() -> bool | str:
    ca = os.getenv("GIGACHAT_CA_BUNDLE")
    if ca:
        return ca
    raw = os.getenv("GIGACHAT_VERIFY_SSL", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_timeout() -> float:
    return float(os.getenv("GIGACHAT_TIMEOUT", "30"))


def make_basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def mask(token: str) -> str:
    if len(token) <= 12:
        return "***"
    return f"{token[:8]}…(len={len(token)})"


def describe(response: requests.Response) -> str:
    ct = response.headers.get("content-type", "?")
    body = response.text[:300].replace("\n", " ")
    return f"status={response.status_code} content-type={ct} body[:300]={body!r}"


def request_token(
    client_id: str,
    client_secret: str,
    scope: str,
    oauth_url: str,
    verify: bool | str,
    timeout: float,
) -> requests.Response:
    headers = {
        "Authorization": make_basic_auth(client_id, client_secret),
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    return requests.post(
        oauth_url,
        headers=headers,
        data={"scope": scope},
        verify=verify,
        timeout=timeout,
    )


def request_chat(
    token: str,
    model: str,
    api_url: str,
    prompt: str,
    verify: bool | str,
    timeout: float,
) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    return requests.post(api_url, headers=headers, json=payload, verify=verify, timeout=timeout)


def step_oauth(args: argparse.Namespace) -> str:
    print("[1/2] OAuth …")
    client_id = get_required("GIGACHAT_CLIENT_ID")
    client_secret = get_required("GIGACHAT_CLIENT_SECRET")
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    oauth_url = os.getenv(
        "GIGACHAT_OAUTH_URL",
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
    )

    verify = get_verify()
    if verify is False:
        print("  [WARN] TLS-проверка отключена (GIGACHAT_VERIFY_SSL=false).")
        print("         Для продакшена задайте GIGACHAT_CA_BUNDLE на PEM с корневыми сертификатами Минцифры.")

    try:
        response = request_token(client_id, client_secret, scope, oauth_url, verify, get_timeout())
    except requests.RequestException as exc:
        print(f"  [FAIL] Сетевая ошибка OAuth: {exc}")
        raise SystemExit(3)

    if response.status_code != 200:
        print(f"  [FAIL] OAuth не 200: {describe(response)}")
        print("         Частые причины: неверные CLIENT_ID/SECRET, не тот scope,")
        print("         прокси/VPN, отсутствие корневых сертификатов Минцифры.")
        raise SystemExit(3)

    try:
        data = response.json()
    except ValueError:
        print(f"  [FAIL] OAuth вернул не JSON: {describe(response)}")
        print("         Обычно это HTML от прокси или подмена ответа, а не отказ ключей.")
        raise SystemExit(3)

    token = data.get("access_token")
    if not token:
        print(f"  [FAIL] В ответе нет access_token: keys={list(data)}")
        raise SystemExit(3)

    print(f"  [OK] Токен получен: {mask(token)}; expires_at={data.get('expires_at')}")
    return token


def step_chat(args: argparse.Namespace, token: str) -> None:
    print("[2/2] Chat …")
    model = args.model or os.getenv("GIGACHAT_MODEL", "GigaChat")
    api_url = os.getenv(
        "GIGACHAT_API_URL",
        "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
    )

    try:
        response = request_chat(token, model, api_url, args.prompt, get_verify(), get_timeout())
    except requests.RequestException as exc:
        print(f"  [FAIL] Сетевая ошибка chat: {exc}")
        raise SystemExit(4)

    if response.status_code != 200:
        print(f"  [FAIL] Chat не 200: {describe(response)}")
        raise SystemExit(4)

    try:
        data = response.json()
    except ValueError:
        print(f"  [FAIL] Chat вернул не JSON: {describe(response)}")
        raise SystemExit(4)

    content = (
        data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(data.get("choices"), list)
        else ""
    )
    print(f"  [OK] model={data.get('model', model)}")
    print(f"  Ответ: {content.strip()[:300]!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Диагностика GigaChat по переменным окружения")
    parser.add_argument("--no-chat", action="store_true", help="Только OAuth, без вызова модели")
    parser.add_argument("--prompt", default="Ответь одним словом: тест", help="Промпт для модели")
    parser.add_argument("--model", default=None, help="Переопределить GIGACHAT_MODEL")
    args = parser.parse_args(argv)

    load_env()
    try:
        token = step_oauth(args)
        if not args.no_chat:
            step_chat(args, token)
    except EnvError as exc:
        print(f"[FAIL] {exc}")
        return 2

    print("[OK] GigaChat доступен")
    return 0


if __name__ == "__main__":
    sys.exit(main())