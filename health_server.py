"""Health endpoint and GitHub App installation callback."""
import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from github_integration import complete_installation, complete_oauth_authorization


class HealthHandler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="text/plain; charset=utf-8"):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/health", "/healthz"):
            self._send(200, "OK\n")
            return
        if parsed.path == "/github/oauth/callback":
            query = parse_qs(parsed.query)
            state = (query.get("state") or [""])[0]
            code = (query.get("code") or [""])[0]
            oauth_error = (query.get("error_description") or query.get("error") or [""])[0]
            if oauth_error:
                self._send(
                    400,
                    "<h2>Авторизация GitHub отменена</h2><p>Вернитесь в Telegram и повторите подключение.</p>",
                    "text/html; charset=utf-8",
                )
                return
            try:
                install_url = complete_oauth_authorization(code, state)
            except Exception as exc:
                safe_error = html.escape(str(exc))
                self._send(
                    400,
                    "<h2>Не удалось проверить GitHub-аккаунт</h2><p>{}</p><p>Вернитесь в Telegram и повторите подключение.</p>".format(safe_error),
                    "text/html; charset=utf-8",
                )
                return
            self.send_response(302)
            self.send_header("Location", install_url)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        if parsed.path == "/github/callback":
            query = parse_qs(parsed.query)
            state = (query.get("state") or [""])[0]
            installation_id = (query.get("installation_id") or [""])[0]
            setup_action = (query.get("setup_action") or [""])[0]
            if setup_action not in ("install", "update") or not state or not installation_id:
                self._send(
                    400,
                    "<h2>Не удалось подключить GitHub</h2><p>GitHub не передал обязательные параметры установки. Вернитесь в Telegram и повторите подключение.</p>",
                    "text/html; charset=utf-8",
                )
                return
            try:
                record = complete_installation(state, installation_id)
            except Exception as exc:
                safe_error = html.escape(str(exc))
                self._send(
                    400,
                    "<h2>Не удалось подключить GitHub</h2><p>{}</p><p>Вернитесь в Telegram, откройте раздел GitHub и повторите подключение.</p>".format(safe_error),
                    "text/html; charset=utf-8",
                )
                return
            login = html.escape(record["account_login"])
            self._send(
                200,
                "<h2>GitHub подключён</h2><p>Аккаунт <b>@{}</b> привязан к Telegram-пользователю.</p><p>Вернитесь в Telegram и откройте раздел GitHub, чтобы увидеть доступные репозитории.</p>".format(login),
                "text/html; charset=utf-8",
            )
            return
        self._send(404, "Not found\n")

    def log_message(self, _format, *_args):
        pass


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
