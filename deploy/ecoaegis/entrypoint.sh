#!/bin/sh
set -eu

database_password_file="${DATABASE_PASSWORD_FILE:-/run/secrets/db_password}"
app_secret_file="${APP_SECRET_FILE:-/run/secrets/app_secret}"
database_host="${DATABASE_HOST:-db}"
database_port="${DATABASE_PORT:-5432}"
database_user="${POSTGRES_USER:-ecoaegis}"
database_name="${POSTGRES_DB:-ecoaegis}"

if [ ! -r "$database_password_file" ]; then
    printf '%s\n' 'database password secret is not readable' >&2
    exit 1
fi
if [ ! -r "$app_secret_file" ]; then
    printf '%s\n' 'application secret is not readable' >&2
    exit 1
fi

database_password=$(tr -d '\r\n' < "$database_password_file")
app_secret=$(tr -d '\r\n' < "$app_secret_file")

case "$database_password" in
    ''|*[!0-9A-Fa-f]*)
        printf '%s\n' 'database password secret must be non-empty hexadecimal text' >&2
        exit 1
        ;;
esac
case "$app_secret" in
    ''|*[!0-9A-Fa-f]*)
        printf '%s\n' 'application secret must be non-empty hexadecimal text' >&2
        exit 1
        ;;
esac
case "$database_user:$database_name" in
    *[!A-Za-z0-9_:]*)
        printf '%s\n' 'database user and name may contain only letters, digits, and underscores' >&2
        exit 1
        ;;
esac

export SECRET_KEY="$app_secret"
export DATABASE_URL="postgresql://${database_user}:${database_password}@${database_host}:${database_port}/${database_name}"

if [ "${ECOAEGIS_TEST_MODE:-false}" = "true" ]; then
    export TEST_DATABASE_URL="$DATABASE_URL"
fi

unset database_password app_secret
exec "$@"
