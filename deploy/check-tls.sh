#!/bin/sh
# Замер для строки приёмки НФ-75: TLS не ниже 1.2, шифров RC4 и 3DES нет.
#
# Строка требует двух вещей: отклонить 1.0 и 1.1, принять 1.2 и 1.3, и записать
# версии с наборами в протокол. Вывод этого скрипта и есть такая запись:
#     sh check-tls.sh localhost > ../docs/protocol-tls.md
#
# Главное доказательство здесь — nmap, а не openssl, и вот почему.
# openssl отказывается собирать ClientHello со старыми версиями: свежие сборки
# вырезают TLS 1.0 и 1.1 на уровне политики. Тогда команда падает с текстом
# "no protocols available" ДО строки CONNECTED — это отказ нашего клиента,
# рукопожатия не было вовсе, и предъявлять такой вывод комиссии нельзя.
# Проверено 15.09.2026 на example.com: без понижения уровня падает клиент,
# с `-cipher DEFAULT@SECLEVEL=0` рукопожатие по TLS 1.0 проходит.
# nmap строит записи TLS сам и версию 0x0301 отправит независимо от того,
# что умеет линкованная библиотека, — поэтому его ответ говорит о сервере.
set -eu

HOST="${1:-localhost}"
PORT="${2:-443}"

# На маке два openssl: /usr/bin/openssl — это LibreSSL, он ведёт себя с флагами
# иначе. Берём тот, что из homebrew, если он есть.
OPENSSL=/opt/homebrew/bin/openssl
[ -x "$OPENSSL" ] || OPENSSL=$(command -v openssl)

echo "# Протокол НФ-75: версии TLS и наборы шифров"
echo
echo "Замер $(date '+%d.%m.%Y %H:%M %Z'), узел \`$HOST:$PORT\`."
echo "Инструменты: \`$(nmap --version 2>/dev/null | head -1)\`, \`$($OPENSSL version)\`."
echo
echo '## Главное доказательство: nmap'
echo
echo 'Ожидание: секции TLSv1.2 и TLSv1.3 со списком наборов есть,'
echo 'секций TLSv1.0 и TLSv1.1 нет вообще.'
echo
echo '```'
nmap --script ssl-enum-ciphers -p "$PORT" "$HOST" 2>&1 || true
echo '```'
echo
echo '## Контроль: четыре форсированных подключения openssl'
echo
echo 'Как читать: текст `no protocols available` ДО строки CONNECTED означает,'
echo 'что отказал наш клиент, — такой замер не засчитывается. Доказательством'
echo 'служит алерт сервера ПОСЛЕ CONNECTED (`alert protocol version`).'
echo

for V in tls1 tls1_1 tls1_2 tls1_3; do
    case "$V" in
        # Понижаем уровень безопасности клиента, иначе он откажет сам за сервер.
        tls1|tls1_1) EXTRA="-cipher DEFAULT@SECLEVEL=0" ;;
        *)           EXTRA="" ;;
    esac
    echo "### -$V"
    echo '```'
    # shellcheck disable=SC2086
    echo | $OPENSSL s_client -connect "$HOST:$PORT" -"$V" $EXTRA 2>&1 \
        | grep -E 'CONNECTED|Protocol|Cipher|alert|no protocols available|handshake failure' \
        | head -6 || true
    echo '```'
    echo
done
