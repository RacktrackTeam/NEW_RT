"""The proofs never write to the Demo Organization (verify pass 2, finding 7): its accounts and its run are the owner's
demo, and a proof's copy-in, planted difference or Confirm would show there as real drift. Proofs sign in with the Test
Organization's accounts (orgownert, admint, techt, admin3) and use its run fx1hyd000001; the platform owner acts in the
run's organization, so with fx1hyd000001 it acts in the Test Organization."""

DEMO_USERS = {"orgowner", "admin", "manager", "tech"}
DEMO_RUNS = {"dm1hyd000001"}


def check(user=None, run=None):
    if user in DEMO_USERS or run in DEMO_RUNS:
        raise SystemExit(f"refused: {user or run} belongs to the Demo Organization, which proofs must not change; "
                         "use orgownert, admint, techt or admin3 and the run fx1hyd000001")
