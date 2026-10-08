#!/bin/sh
# Encrypt the newest complete backup and copy it off-host (BACKUP_AND_RESTORE.md "Off-site copies").
# Runs on the Docker host (cron, after the nightly backup), NOT inside the portal containers.
#
#   AGE_RECIPIENTS_FILE  public key(s) of the backup operators (private key kept OFF this host)
#   OFFSITE_TARGET       rclone remote, e.g. "offsite:school-portal-backups"
#   BACKUP_VOLUME_DIR    host path of the "backups" volume (docker volume inspect school-portal_backups)
set -eu

: "${AGE_RECIPIENTS_FILE:?AGE_RECIPIENTS_FILE required}"
: "${OFFSITE_TARGET:?OFFSITE_TARGET required}"
: "${BACKUP_VOLUME_DIR:?BACKUP_VOLUME_DIR required}"

latest=$(find "$BACKUP_VOLUME_DIR" -mindepth 1 -maxdepth 1 -type d -name 'portal-*' ! -name '*.partial' \
          -exec test -f '{}/manifest.json' ';' -print | sort | tail -n 1)
[ -n "$latest" ] || { echo "no complete backup found" >&2; exit 1; }

name=$(basename "$latest")
umask 077
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# One encrypted archive per backup; plaintext never leaves this host.
tar -C "$BACKUP_VOLUME_DIR" -cf - "$name" | age -R "$AGE_RECIPIENTS_FILE" -o "$tmp/$name.tar.age"
sha256sum "$tmp/$name.tar.age" > "$tmp/$name.tar.age.sha256"
rclone copy --immutable "$tmp/" "$OFFSITE_TARGET/"
echo "copied $name.tar.age to $OFFSITE_TARGET"
