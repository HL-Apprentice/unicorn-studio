#!/bin/bash
# Scan the working tree AND full git history for credentials and private data.
# Prints only file names and match counts - never the matched values.
#   tools/scan-secrets.sh [private-terms-file ...]
# A private-terms file (kept OUTSIDE the repo, chmod 600) lists exact strings that must never appear:
# your Wi-Fi name, broker address, passwords, device id, names, e-mail addresses... one per line.
# KEY=VALUE lines (e.g. a secrets.env or secrets.py) are accepted too: only the values are used.
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")/..")"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
PAT='(^|[^0-9.])(192\.168|10\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}([^0-9.]|$)
[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]{2,}
/Users/[A-Za-z]
/home/[a-z]
BEGIN [A-Z ]*PRIVATE KEY
(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}
AKIA[0-9A-Z]{16}
sk-[A-Za-z0-9]{20,}
xox[abpr]-[A-Za-z0-9-]{10,}
unicorn[_/][0-9a-f]{6}([^0-9a-z]|$)
(PASSWORD|PASS|PSK|TOKEN|SECRET|OTA_KEY)[A-Z_]*[ ]*[=:][ ]*["'"'"']?[A-Za-z0-9!#$%&*+,./:;<>?@^_~-]{6,}'
ALLOW='SECRETS_FILE=|@micropython\.(viper|native)|@users\.noreply\.github\.com|change-me|your-wifi|long-random-string|same-value-as|unicorn[_/]abc123|unicorn[_/]<|192\.0\.2\.|example\.(com|org)|homeassistant\.local|pip install|PASSWORD=$|github\.com/pimoroni'
printf '%s\n' "$PAT" > "$T/pat"
: > "$T/priv"
for f in "$@"; do
  [ -r "$f" ] || { echo "cannot read $f"; exit 2; }
  python3 - "$f" >> "$T/priv" <<'PY'
import sys
for line in open(sys.argv[1], errors="ignore"):
    s = line.strip()
    if not s or s.startswith("#"): continue
    if "=" in s and s.split("=", 1)[0].strip().replace("export ", "").replace("_", "").isalnum() and s.split("=",1)[0].strip().isupper():
        s = s.split("=", 1)[1].strip().strip("'\"")
    if len(s) >= 4: print(s)
PY
done
nterms=$(wc -l < "$T/priv" | tr -d ' ')
echo "private terms loaded: $nterms (values not shown)"

scan() {  # $1 = label, stdin = text stream
  cat > "$T/blob"
  local a b
  a=$(grep -a -I -n -E -f "$T/pat" "$T/blob" | grep -v -E "$ALLOW" | wc -l | tr -d ' ')
  b=0; [ -s "$T/priv" ] && b=$(grep -a -c -i -F -f "$T/priv" "$T/blob")
  echo "$1: pattern hits $a, private-term hits $b"
  [ "$a$b" != "00" ] && fail=1
  return 0
}
# working tree, per file (names only)
git ls-files -co --exclude-standard 2>/dev/null > "$T/files" || find . -type f -not -path './.git/*' > "$T/files"
while IFS= read -r f; do
  [ -f "$f" ] || continue
  a=$(grep -a -I -n -E -f "$T/pat" "$f" | grep -v -E "$ALLOW" | wc -l | tr -d ' ')
  b=0; [ -s "$T/priv" ] && b=$(grep -a -c -i -F -f "$T/priv" "$f")
  [ "$a$b" != "00" ] && { echo "  FLAG $f (pattern $a, private $b)"; fail=1; }
done < "$T/files"
echo "working tree: $(wc -l < "$T/files" | tr -d ' ') files scanned"
if git rev-parse --git-dir >/dev/null 2>&1 && git rev-parse HEAD >/dev/null 2>&1; then
  git log -p --all --binary --format='commit %H%nauthor %an <%ae>%ncommitter %cn <%ce>%n%B' > "$T/hist"
  scan "git history (patches, messages, authors)" < "$T/hist"
fi
[ $fail = 0 ] && echo "RESULT: clean" || echo "RESULT: FLAGGED - inspect the files above"
exit $fail
