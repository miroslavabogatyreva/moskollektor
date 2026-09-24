# Демонстрационный каталог LDAP для НФ-76 (MOS-39). Не образ с Docker Hub —
# slapd берём из штатного пакета Debian: тот же дистрибутив, что в стеке
# заказчика (docs/HLD.md разд. 7.3), версия и лицензия не требуют отдельной
# сверки на "заброшенность", их несёт сам релиз trixie. Лицензия slapd —
# OpenLDAP Public License (BSD-подобная, разрешена HLD 7.3).
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends slapd ldap-utils \
    && rm -rf /var/lib/apt/lists/* /etc/ldap/slapd.d /var/lib/ldap/*
COPY deploy/ldap/slapd.conf /etc/ldap/slapd.conf
COPY deploy/ldap/bootstrap.ldif /etc/ldap/bootstrap.ldif
COPY deploy/ldap/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# Порт не публикуется наружу контейнера в docker-compose.yml (только сеть
# compose) — EXPOSE тут документирует порт, а не открывает его хосту.
EXPOSE 389
ENTRYPOINT ["/entrypoint.sh"]
