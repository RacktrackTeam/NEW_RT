/**
 * The setup gate, decided in one place.
 *
 * GET /api/auth/me hands the client `user.setup = { needsSetup, blocked,
 * reason }` (server/lib/estate.setupSummary). One thing reads it: an owner
 * or org admin whose organization still lacks a required item is sent to
 * /setup instead of Scan, where the guided flow opens.
 *
 * Nobody else is ever gated. The admin finishes setup when the organization
 * is created and only then invites technicians, so a member or site manager
 * opens the app straight into Scan whatever the field says.
 *
 * A user record WITHOUT the field, from a server that predates it or a
 * cached session from before /me answered, is treated as ready. The gate
 * never blocks on an absence.
 */

export function isSetupAdmin(user) {
  return user?.role === 'owner' || user?.role === 'org_admin';
}

/**
 * @returns {'setup' | 'ok'}
 *   setup    send to /setup (admins only)
 *   ok       let them through
 */
export function setupDecision(user) {
  const s = user?.setup;
  if (!s || typeof s !== 'object') return 'ok';
  if (!isSetupAdmin(user)) return 'ok';
  if (s.needsSetup) return 'setup';
  return 'ok';
}

/** True when the record has not been told about setup at all yet. */
export function setupUnknown(user) {
  return !!user && user.setup === undefined;
}
