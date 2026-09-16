# Протокол НФ-75: версии TLS и наборы шифров

Замер 16.09.2026 04:24 UTC, узел `[::1]:443`, адрес вида IPv6.
Инструменты: `Nmap version 7.94SVN ( https://nmap.org )`, `OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13 30 Jan 2024)`.

## Главное доказательство: nmap

Ожидание: секции TLSv1.2 и TLSv1.3 со списком наборов есть,
секций TLSv1.0 и TLSv1.1 нет вообще.

```
Starting Nmap 7.94SVN ( https://nmap.org ) at 2026-09-16 04:24 UTC
Nmap scan report for ::1
Host is up (0.00010s latency).

PORT    STATE SERVICE
443/tcp open  https
| ssl-enum-ciphers: 
|   TLSv1.2: 
|     ciphers: 
|       TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256 (secp256r1) - A
|       TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 (secp256r1) - A
|       TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256 (secp256r1) - A
|     compressors: 
|       NULL
|     cipher preference: client
|   TLSv1.3: 
|     ciphers: 
|       TLS_AKE_WITH_AES_128_GCM_SHA256 (secp256r1) - A
|       TLS_AKE_WITH_AES_256_GCM_SHA384 (secp256r1) - A
|       TLS_AKE_WITH_CHACHA20_POLY1305_SHA256 (secp256r1) - A
|     cipher preference: client
|_  least strength: A

Nmap done: 1 IP address (1 host up) scanned in 0.18 seconds
```

## Контроль: четыре форсированных подключения openssl

Как читать: смотреть на строку «Итог», а не на строку `Protocol`.
openssl печатает в `Protocol` запрошенную версию, а не согласованную, поэтому
при отказе сервера там всё равно стоит `TLSv1` — и без строки «Итог» протокол
читается как «TLS 1.0 прошёл». Настоящий признак отказа сервера — текст
`alert protocol version` и `Cipher is (NONE)`; признак отказа нашего клиента —
`no protocols available`, при нём рукопожатия не было и замер не засчитывается.
Порядок строк внутри блока ни о чём не говорит: ошибки идут в поток ошибок
и смешиваются с обычным выводом не в том порядке, в каком происходили.

### -tls1

**Итог: СЕРВЕР ОТКЛОНИЛ — это и есть доказательство для НФ-75**

```
40A73B25077A0000:error:0A00042E:SSL routines:ssl3_read_bytes:tlsv1 alert protocol version:../ssl/record/rec_layer_s3.c:1599:SSL alert number 70
CONNECTED(00000003)
New, (NONE), Cipher is (NONE)
    Protocol  : TLSv1
    Cipher    : 0000
```

### -tls1_1

**Итог: СЕРВЕР ОТКЛОНИЛ — это и есть доказательство для НФ-75**

```
4037C5F85D720000:error:0A00042E:SSL routines:ssl3_read_bytes:tlsv1 alert protocol version:../ssl/record/rec_layer_s3.c:1599:SSL alert number 70
CONNECTED(00000003)
New, (NONE), Cipher is (NONE)
    Protocol  : TLSv1.1
    Cipher    : 0000
```

### -tls1_2

**Итог: СЕРВЕР ПРИНЯЛ — TLSv1.2, Cipher is ECDHE-RSA-AES256-GCM-SHA384**

```
CONNECTED(00000003)
New, TLSv1.2, Cipher is ECDHE-RSA-AES256-GCM-SHA384
    Protocol  : TLSv1.2
    Cipher    : ECDHE-RSA-AES256-GCM-SHA384
```

### -tls1_3

**Итог: СЕРВЕР ПРИНЯЛ — TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384**

```
CONNECTED(00000003)
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
```

