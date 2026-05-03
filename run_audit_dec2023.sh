#!/usr/bin/env bash
set -u
rm -f /tmp/audit_engine.log /tmp/audit_engine.exit
{
  echo "=== adversarial ==="
  python3 -u audit_engine_adversarial.py
  echo "=== harness ==="
  AUDIT_BARS="${AUDIT_BARS:-1500}" python3 -u audit_engine_dec2023.py --prefix baseline
} > /tmp/audit_engine.log 2> /tmp/audit_engine.err
ec=$?
echo "$ec" > /tmp/audit_engine.exit
echo "audit exited $ec"
echo "log: $(wc -l < /tmp/audit_engine.log) lines"
tail -60 /tmp/audit_engine.log
exit $ec
