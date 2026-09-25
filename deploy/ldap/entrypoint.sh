#!/bin/sh
# Демонстрационный каталог — состояние не персистентно: при каждом старте
# контейнера каталог загружается заново из bootstrap.ldif, а не из тома.
# Так «Проверить соединение» и учебный прогон НФ-76 всегда стартуют
# с известного состояния (block-user.sh снимается пересозданием контейнера).
set -eu

rm -rf /var/lib/ldap/*
mkdir -p /var/run/slapd
slapadd -f /etc/ldap/slapd.conf -l /etc/ldap/bootstrap.ldif
chown -R openldap:openldap /var/lib/ldap /var/run/slapd

exec slapd -f /etc/ldap/slapd.conf -h "ldap:///" -u openldap -g openldap -d 0
