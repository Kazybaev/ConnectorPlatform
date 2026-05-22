from __future__ import annotations

import json
import html
import urllib.parse
import urllib.request

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.auth_service import AuthUser, get_auth_service
from app.utils.config import get_settings

router = APIRouter(include_in_schema=False)


def auth_page(title: str, body: str) -> str:
    """Render a focused auth page without depending on the existing app screens."""
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} | AI Connector</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Space Grotesk", Arial, sans-serif;
      background: #f4f7f5;
      color: #17211b;
    }}
    .auth-shell {{
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(320px, 0.9fr) minmax(360px, 1.1fr);
    }}
    .auth-brand {{
      padding: 48px;
      background: #10251b;
      color: #f7fff9;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 36px;
    }}
    .auth-brand img {{ width: 172px; height: auto; }}
    .auth-brand h1 {{ margin: 0; font-size: clamp(34px, 5vw, 62px); line-height: 0.95; }}
    .auth-brand p {{ max-width: 520px; color: #c6d8cc; font-size: 18px; line-height: 1.55; }}
    .auth-panel {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 32px;
    }}
    .auth-card {{
      width: min(100%, 460px);
      background: #ffffff;
      border: 1px solid #dfe8e2;
      border-radius: 8px;
      padding: 30px;
      box-shadow: 0 24px 60px rgba(20, 39, 29, 0.12);
    }}
    .auth-card h2 {{ margin: 0 0 8px; font-size: 30px; line-height: 1.1; }}
    .auth-card p {{ margin: 0 0 22px; color: #5a6b61; line-height: 1.5; }}
    label {{ display: grid; gap: 8px; margin-bottom: 14px; color: #2c3d33; font-weight: 600; }}
    input {{
      width: 100%;
      border: 1px solid #cad8d0;
      border-radius: 8px;
      padding: 13px 14px;
      font: inherit;
      outline: none;
    }}
    input:focus {{ border-color: #168a4f; box-shadow: 0 0 0 3px rgba(22, 138, 79, 0.14); }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      min-height: 46px;
      border-radius: 8px;
      border: 1px solid transparent;
      padding: 12px 16px;
      font: inherit;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }}
    .button-primary {{ background: #168a4f; color: #fff; }}
    .button-secondary {{ background: #fff; color: #17211b; border-color: #cad8d0; }}
    .auth-actions {{ display: grid; gap: 12px; margin-top: 18px; }}
    .auth-links {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; margin-top: 18px; }}
    .auth-links a {{ color: #136f43; font-weight: 700; text-decoration: none; }}
    .notice {{
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 16px;
      background: #eef7f1;
      color: #255b38;
      border: 1px solid #cfe9d7;
      overflow-wrap: anywhere;
    }}
    .notice-error {{ background: #fff1f1; color: #8d2525; border-color: #f0caca; }}
    .divider {{ display: flex; align-items: center; gap: 12px; margin: 18px 0; color: #7a8a81; }}
    .divider::before, .divider::after {{ content: ""; height: 1px; flex: 1; background: #dfe8e2; }}
    @media (max-width: 860px) {{
      .auth-shell {{ grid-template-columns: 1fr; }}
      .auth-brand {{ padding: 30px; }}
      .auth-panel {{ padding: 24px; }}
    }}
  </style>
</head>
<body>
  <div class="auth-shell">
    <section class="auth-brand">
      <a href="/" aria-label="AI Connector">
        <img src="/static/image.png?v=logo-20260520" alt="AI Connector" />
      </a>
      <div>
        <h1>AI Connector</h1>
        <p>Личный аккаунт для WhatsApp, ботов, проектов и операторского inbox. Каждый клиент работает через свою учетную запись.</p>
      </div>
    </section>
    <main class="auth-panel">
      <section class="auth-card">
        {body}
      </section>
    </main>
  </div>
</body>
</html>"""


def set_session_cookie(response: RedirectResponse, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.auth_session_cookie_name,
        token,
        httponly=True,
        secure=settings.platform_public_base_url.startswith("https://"),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


def redirect_after_login(token: str) -> RedirectResponse:
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, token)
    return response


def render_login(error: str = "", notice: str = "") -> HTMLResponse:
    auth_service = get_auth_service()
    google_button = (
        '<a class="button button-secondary" href="/auth/google">Войти через Google</a>'
        if auth_service.google_oauth_enabled
        else '<button class="button button-secondary" type="button" disabled>Google вход не настроен</button>'
    )
    body = f"""
      <h2>Вход</h2>
      <p>Войдите, чтобы открыть WhatsApp, чаты и подключение ботов.</p>
      {f'<div class="notice">{notice}</div>' if notice else ''}
      {f'<div class="notice notice-error">{error}</div>' if error else ''}
      <form method="post" action="/login">
        <label>Email<input type="email" name="email" autocomplete="email" required /></label>
        <label>Пароль<input type="password" name="password" autocomplete="current-password" required /></label>
        <div class="auth-actions">
          <button class="button button-primary" type="submit">Войти</button>
        </div>
      </form>
      <div class="divider">или</div>
      {google_button}
      <div class="auth-links">
        <a href="/register">Создать аккаунт</a>
        <a href="/forgot-password">Забыли пароль?</a>
      </div>
    """
    return HTMLResponse(auth_page("Вход", body))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = current_user_from_request(request)
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    notice = "Пароль обновлен. Теперь можно войти." if request.query_params.get("reset") == "done" else ""
    return render_login(notice=notice)


@router.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    user = get_auth_service().authenticate_password(email=email, password=password)
    if user is None:
        return render_login(error="Неверный email или пароль.")
    return redirect_after_login(get_auth_service().create_session(user.id))


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    user = current_user_from_request(request)
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    body = """
      <h2>Регистрация</h2>
      <p>Создайте аккаунт клиента. После регистрации откроется платформа.</p>
      <form method="post" action="/register">
        <label>Имя или компания<input name="full_name" autocomplete="organization" required /></label>
        <label>Email<input type="email" name="email" autocomplete="email" required /></label>
        <label>Пароль<input type="password" name="password" autocomplete="new-password" minlength="8" required /></label>
        <div class="auth-actions">
          <button class="button button-primary" type="submit">Создать аккаунт</button>
        </div>
      </form>
      <div class="auth-links">
        <a href="/login">Уже есть аккаунт?</a>
      </div>
    """
    return HTMLResponse(auth_page("Регистрация", body))


@router.post("/register")
def register(
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    try:
        user = get_auth_service().create_user(email=email, password=password, full_name=full_name)
    except ValueError as exc:
        body = f"""
          <h2>Регистрация</h2>
          <p>Проверьте данные и попробуйте еще раз.</p>
          <div class="notice notice-error">{exc}</div>
          <form method="post" action="/register">
            <label>Имя или компания<input name="full_name" value="{html.escape(full_name.strip())}" required /></label>
            <label>Email<input type="email" name="email" value="{html.escape(email.strip())}" required /></label>
            <label>Пароль<input type="password" name="password" minlength="8" required /></label>
            <div class="auth-actions"><button class="button button-primary" type="submit">Создать аккаунт</button></div>
          </form>
          <div class="auth-links"><a href="/login">Уже есть аккаунт?</a></div>
        """
        return HTMLResponse(auth_page("Регистрация", body), status_code=status.HTTP_400_BAD_REQUEST)
    return redirect_after_login(get_auth_service().create_session(user.id))


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page() -> HTMLResponse:
    body = """
      <h2>Сброс пароля</h2>
      <p>Введите email. Система создаст ссылку для сброса пароля.</p>
      <form method="post" action="/forgot-password">
        <label>Email<input type="email" name="email" autocomplete="email" required /></label>
        <div class="auth-actions">
          <button class="button button-primary" type="submit">Получить ссылку</button>
        </div>
      </form>
      <div class="auth-links"><a href="/login">Вернуться ко входу</a></div>
    """
    return HTMLResponse(auth_page("Сброс пароля", body))


@router.post("/forgot-password")
def forgot_password(email: str = Form(...)) -> HTMLResponse:
    token = get_auth_service().create_password_reset_token(email)
    reset_link = f"/reset-password?token={urllib.parse.quote(token)}" if token else ""
    reset_notice = (
        f'Ссылка для сброса: <a href="{reset_link}">{reset_link}</a>'
        if reset_link
        else "Если аккаунт существует, ссылка для сброса будет создана."
    )
    body = f"""
      <h2>Проверьте сброс</h2>
      <div class="notice">{reset_notice}</div>
      <p>Для продакшена сюда нужно подключить email-провайдера, чтобы отправлять ссылку клиенту письмом.</p>
      <div class="auth-links"><a href="/login">Вернуться ко входу</a></div>
    """
    return HTMLResponse(auth_page("Сброс пароля", body))


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(token: str = "") -> HTMLResponse:
    body = f"""
      <h2>Новый пароль</h2>
      <p>Введите новый пароль для аккаунта.</p>
      <form method="post" action="/reset-password">
        <input type="hidden" name="token" value="{html.escape(token)}" />
        <label>Новый пароль<input type="password" name="password" autocomplete="new-password" minlength="8" required /></label>
        <div class="auth-actions">
          <button class="button button-primary" type="submit">Обновить пароль</button>
        </div>
      </form>
    """
    return HTMLResponse(auth_page("Новый пароль", body))


@router.post("/reset-password")
def reset_password(token: str = Form(...), password: str = Form(...)):
    try:
        user = get_auth_service().reset_password(token=token, password=password)
    except ValueError as exc:
        return HTMLResponse(
            auth_page("Новый пароль", f'<h2>Новый пароль</h2><div class="notice notice-error">{exc}</div>'),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if user is None:
        return HTMLResponse(
            auth_page("Новый пароль", '<h2>Новый пароль</h2><div class="notice notice-error">Ссылка недействительна или истекла.</div>'),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse("/login?reset=done", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    settings = get_settings()
    token = request.cookies.get(settings.auth_session_cookie_name, "")
    get_auth_service().delete_session(token)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.auth_session_cookie_name)
    return response


@router.get("/auth/google")
def google_login() -> RedirectResponse:
    settings = get_settings()
    if not get_auth_service().google_oauth_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google OAuth is not configured.")

    state_value = get_auth_service().create_oauth_state()
    redirect_uri = settings.google_oauth_redirect_uri or f"{settings.platform_public_base_url}/auth/google/callback"
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_value,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/google/callback")
def google_callback(code: str = "", state: str = "") -> RedirectResponse:
    settings = get_settings()
    auth_service = get_auth_service()
    if not auth_service.google_oauth_enabled or not auth_service.consume_oauth_state(state):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    redirect_uri = settings.google_oauth_redirect_uri or f"{settings.platform_public_base_url}/auth/google/callback"
    token_data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        token_payload = json.loads(response.read().decode("utf-8"))

    access_token = str(token_payload.get("access_token", "")).strip()
    if not access_token:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    user_request = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(user_request, timeout=15) as response:
        profile = json.loads(response.read().decode("utf-8"))

    user = auth_service.upsert_google_user(
        email=str(profile.get("email", "")),
        full_name=str(profile.get("name", "")),
        google_sub=str(profile.get("sub", "")),
    )
    return redirect_after_login(auth_service.create_session(user.id))


def current_user_from_request(request: Request) -> AuthUser | None:
    settings = get_settings()
    token = request.cookies.get(settings.auth_session_cookie_name, "")
    return get_auth_service().get_user_by_session(token)
