#!/bin/sh
set -eu
umask 077

backup_dir="${BACKUP_DIR:-/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
password_file="${DATABASE_PASSWORD_FILE:-/run/secrets/db_password}"
database_host="${DATABASE_HOST:-db}"
database_user="${POSTGRES_USER:-ecoaegis}"
database_name="${POSTGRES_DB:-ecoaegis}"

case "$backup_dir" in
    /backups|/backups/*) ;;
    *)
        printf '%s\n' 'BACKUP_DIR must stay within /backups' >&2
        exit 1
        ;;
esac
case "$retention_days" in
    ''|*[!0-9]*)
        printf '%s\n' 'BACKUP_RETENTION_DAYS must be a non-negative integer' >&2
        exit 1
        ;;
esac
if [ ! -r "$password_file" ]; then
    printf '%s\n' 'database password secret is not readable' >&2
    exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
database_name_out="ecoaegis-${stamp}.dump"
attachments_name_out="attachments-${stamp}.tar.gz"
evidence_name_out="evidence-${stamp}.tar.gz"
manifest_name_out="manifest-${stamp}.sha256"

database_tmp="${backup_dir}/.${database_name_out}.tmp"
attachments_tmp="${backup_dir}/.${attachments_name_out}.tmp"
evidence_tmp="${backup_dir}/.${evidence_name_out}.tmp"
manifest_tmp="${backup_dir}/.${manifest_name_out}.tmp"

cleanup() {
    rm -f -- "$database_tmp" "$attachments_tmp" "$evidence_tmp" "$manifest_tmp"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$backup_dir"
export PGPASSWORD
PGPASSWORD=$(tr -d '\r\n' < "$password_file")
pg_dump \
    --host "$database_host" \
    --username "$database_user" \
    --dbname "$database_name" \
    --format custom \
    --compress 9 \
    --no-password \
    --file "$database_tmp"
unset PGPASSWORD

tar -C /attachments -czf "$attachments_tmp" .
tar -C /evidence -czf "$evidence_tmp" .

mv "$database_tmp" "${backup_dir}/${database_name_out}"
mv "$attachments_tmp" "${backup_dir}/${attachments_name_out}"
mv "$evidence_tmp" "${backup_dir}/${evidence_name_out}"

(
    cd "$backup_dir"
    sha256sum "$database_name_out" "$attachments_name_out" "$evidence_name_out"
) > "$manifest_tmp"
mv "$manifest_tmp" "${backup_dir}/${manifest_name_out}"

find "$backup_dir" -maxdepth 1 -type f \
    \( -name 'ecoaegis-*.dump' -o -name 'attachments-*.tar.gz' \
       -o -name 'evidence-*.tar.gz' -o -name 'manifest-*.sha256' \) \
    -mtime "+${retention_days}" -delete

trap - EXIT HUP INT TERM
printf 'backup_utc=%s database=%s attachments=%s evidence=%s manifest=%s\n' \
    "$stamp" "$database_name_out" "$attachments_name_out" "$evidence_name_out" "$manifest_name_out"
