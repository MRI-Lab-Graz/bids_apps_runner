import secrets
import time
from typing import Any, Callable

from flask import jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


def register_auth_handlers(
    app,
    *,
    login_enabled: Callable[[], bool],
    login_password_hash: Callable[[], str],
    auth_token: Callable[[], str],
    auth_header: str,
    csrf_header: str,
    request_auth_token: Callable[[], str],
    request_is_loopback: Callable[[str], bool],
    public_paths: set[str],
    index_endpoint: str = "index",
    login_template: str = "login.html",
):
    def has_valid_remote_token() -> bool:
        configured = auth_token()
        if not configured:
            return False
        provided_token = request_auth_token()
        return bool(provided_token) and secrets.compare_digest(provided_token, configured)

    def request_wants_json_response() -> bool:
        accept = str(request.headers.get("Accept") or "").lower()
        sec_fetch_mode = str(request.headers.get("Sec-Fetch-Mode") or "").lower()
        return bool(
            request.is_json
            or request.path != "/"
            or request.method != "GET"
            or "application/json" in accept
            or sec_fetch_mode not in {"", "navigate"}
        )

    def is_public_request_path(path: str) -> bool:
        static_prefix = f"{app.static_url_path.rstrip('/')}/"
        return path in public_paths or path.startswith(static_prefix)

    def ensure_csrf_token() -> str:
        token = str(session.get("csrf_token") or "").strip()
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    def rotate_csrf_token() -> str:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
        return token

    def safe_next_path(candidate: Any) -> str:
        text = str(candidate or "").strip()
        if text.startswith("/") and not text.startswith("//"):
            return text
        return url_for(index_endpoint)

    def request_supplied_csrf_token() -> str:
        header_token = str(request.headers.get(csrf_header) or "").strip()
        if header_token:
            return header_token

        form_token = str(request.form.get("csrf_token") or "").strip()
        if form_token:
            return form_token

        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            return str(payload.get("csrf_token") or "").strip()

        return ""

    def is_authenticated_session() -> bool:
        return (not login_enabled()) or bool(session.get("authenticated"))

    def unauthenticated_response():
        if request_wants_json_response():
            return jsonify({"error": "Authentication required", "login_url": url_for("login")}), 401

        next_path = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=next_path))

    @app.context_processor
    def inject_security_template_context():
        return {
            "prism_auth_enabled": login_enabled(),
            "prism_is_authenticated": is_authenticated_session(),
            "prism_csrf_token": ensure_csrf_token() if login_enabled() else "",
        }

    @app.after_request
    def add_security_headers(response):
        if login_enabled():
            response.headers[csrf_header] = ensure_csrf_token()
        return response

    @app.before_request
    def protect_gui_access():
        if is_public_request_path(request.path):
            return None

        if has_valid_remote_token():
            return None

        if request_is_loopback(request.remote_addr):
            return None

        if not login_enabled():
            response = jsonify(
                {
                    "error": "Unauthorized",
                    "details": f"Provide {auth_header} or Authorization: Bearer <token>",
                }
            )
            response.headers["WWW-Authenticate"] = (
                f'Bearer realm="BIDS Apps Runner", header="{auth_header}"'
            )
            return response, 401

        if is_authenticated_session():
            return None

        return unauthenticated_response()

    @app.before_request
    def enforce_csrf_protection():
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        if request.path == "/health":
            return None
        if has_valid_remote_token() or not login_enabled():
            return None

        expected_token = ensure_csrf_token()
        provided_token = request_supplied_csrf_token()
        if provided_token and secrets.compare_digest(provided_token, expected_token):
            return None

        if request_wants_json_response():
            return jsonify({"error": "CSRF token missing or invalid"}), 400
        return "CSRF token missing or invalid", 400

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not login_enabled():
            return redirect(url_for(index_endpoint))

        next_path = safe_next_path(request.values.get("next"))
        wants_json = bool(
            request.is_json
            or "application/json" in str(request.headers.get("Accept") or "").lower()
        )
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            password = str(
                request.form.get("password")
                or (payload.get("password") if isinstance(payload, dict) else "")
                or ""
            )
            if not password or not check_password_hash(login_password_hash(), password):
                if wants_json:
                    return jsonify({"error": "Invalid password"}), 401
                return (
                    render_template(
                        login_template,
                        next_path=next_path,
                        login_error="Invalid password",
                    ),
                    401,
                )

            session.clear()
            session["authenticated"] = True
            session["authenticated_at"] = int(time.time())
            rotate_csrf_token()
            if wants_json:
                return jsonify({"message": "Login successful", "redirect": next_path}), 200
            return redirect(next_path)

        if is_authenticated_session():
            return redirect(next_path)

        return render_template(login_template, next_path=next_path, login_error=None)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        if request_wants_json_response():
            return jsonify({"message": "Logged out", "redirect": url_for("login")}), 200
        return redirect(url_for("login"))
