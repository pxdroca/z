"""
generate_session_string.py
===========================
Utilitário de uso único, rodado LOCALMENTE: gera uma Telethon StringSession
a partir do seu login (pede telefone + código, como o listener.py faz da
primeira vez) e imprime a string pronta para colar no secret
TELEGRAM_SESSION_STRING do GitHub Actions.

Por quê: o runner do GitHub Actions é uma máquina nova a cada execução, sem
disco persistente e sem terminal interativo para digitar telefone/código —
então a sessão precisa vir pronta, como uma string, via secret.

⚠️ Trate a string gerada como uma senha: quem tiver acesso a ela consegue
logar na sua conta do Telegram sem precisar do código de verificação. Nunca
cole no código-fonte nem em nenhum lugar público — apenas no campo de
secret do GitHub Actions.

Uso:
    python generate_session_string.py
"""

from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import settings


def main() -> None:
    if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        raise RuntimeError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH não configurados no .env. "
            "Veja o passo a passo no README.md para obtê-los em https://my.telegram.org"
        )

    with TelegramClient(StringSession(), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH) as client:
        session_string = client.session.save()

    print("\nSessão gerada com sucesso. Copie a linha abaixo (é só uma linha,")
    print("mesmo que apareça quebrada no seu terminal) e cole no secret")
    print("TELEGRAM_SESSION_STRING do GitHub Actions:\n")
    print(session_string)
    print("\n⚠️  Guarde com o mesmo cuidado de uma senha — quem tiver essa")
    print("string consegue acessar sua conta do Telegram sem o código de verificação.")


if __name__ == "__main__":
    main()
