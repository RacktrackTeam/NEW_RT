#!/bin/sh
# Start the sign-in and organizations service (server/auth/auth_service.js):
# RT-EOS's sign-in, organizations, sites, people, invites, settings and the
# platform owner's console, and the RT-EOS screens for them at /org/.
# Start this first, then ./engine.sh. No model is loaded, so no model lock.
#
#   ./auth.sh                 start on the PORT in data/auth/.env (3302 when made here)
#
# First start: makes data/auth/.env (a new JWT_SECRET, the database path, the
# mail outbox), builds the /org/ screens into web/org/dist-org, and
# seeds the owner, one organization, one site, its admin and one technician
# (password from RT_SEED_PASSWORD, else generated; kept in
# data/auth/seed-password.txt, mode 600). Stop it with kill $(lsof -ti TCP:<port>).

HERE=$(cd "$(dirname "$0")" && pwd)
AUTH_DIR="$HERE/data/auth"
ENV_FILE="$AUTH_DIR/.env"
NODE_MODULES="$HERE/_cache/rt_eos_node_modules"
mkdir -p "$AUTH_DIR/db" "$AUTH_DIR/outbox"
chmod 700 "$AUTH_DIR"

# node_modules links into the work-area cache (git ignores them).
[ -e "$HERE/server/auth/node_modules" ] || ln -s "$NODE_MODULES/server" "$HERE/server/auth/node_modules"
[ -e "$HERE/web/org/node_modules" ] || ln -s "$NODE_MODULES/client" "$HERE/web/org/node_modules"

if [ ! -f "$ENV_FILE" ]; then
  SECRET=$(node -e "process.stdout.write(require('crypto').randomBytes(48).toString('hex'))")
  umask 077
  cat > "$ENV_FILE" <<ENV
# The sign-in service's settings (made by auth.sh). The engine reads JWT_SECRET
# and RACKTRACK_AUTH_DB from here too. Never commit this file.
PORT=${RT_AUTH_PORT:-3302}
JWT_SECRET=$SECRET
RACKTRACK_AUTH_DB=$AUTH_DIR/db/auth.db
RT_MAIL_OUTBOX=$AUTH_DIR/outbox
CORS_ALLOWED_ORIGINS=http://127.0.0.1:8700,http://localhost:8700,http://127.0.0.1:8701,http://localhost:8701
COOKIE_SECURE=false
# Optional: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM to send mail for real.
ENV
  echo "auth.sh: made $ENV_FILE"
fi
export RT_ENV_FILE="$ENV_FILE"

# The RT-EOS screens under /org/ (built once).
if [ ! -f "$HERE/web/org/dist-org/index.html" ]; then
  echo "auth.sh: building the /org/ screens"
  (cd "$HERE/web/org" && VITE_ORG_ONLY=1 ./node_modules/.bin/vite build --base /org/ --outDir dist-org --emptyOutDir) >/dev/null || echo "auth.sh: the /org/ build failed; the service starts without it"
fi

DB=$(grep '^RACKTRACK_AUTH_DB=' "$ENV_FILE" | cut -d= -f2-)
if [ ! -f "$DB" ]; then
  (cd "$HERE/server/auth" && RACKTRACK_AUTH_DB="$DB" node scripts/seed-auth.js) || exit 1
fi

cd "$HERE/server/auth" || exit 1
exec node auth_service.js
