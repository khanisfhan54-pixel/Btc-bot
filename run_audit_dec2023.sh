#!/usr/bin/env bash
set -u
rm -f /tmp/audit_dec.log /tmp/audit_dec.exit
python3 -u audit_run_dec2023.py \
  > /tmp/audit_dec.log 2> /tmp/audit_dec.err
ec=$?
echo "$ec" > /tmp/audit_dec.exit
echo "audit_run_dec2023.py exited $ec"
echo "log: $(wc -l < /tmp/audit_dec.log) lines"
tail -40 /tmp/audit_dec.log
exit $ec
