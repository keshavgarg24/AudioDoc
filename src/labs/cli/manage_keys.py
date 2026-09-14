"""API key administration.

    labs-keys create --name "web frontend" --scopes analyze,read
    labs-keys create --name "partner" --quota 500
    labs-keys list
    labs-keys revoke --fingerprint labs_live_AbC123...

The raw key is printed once at creation and cannot be recovered afterwards;
only its SHA-256 digest is stored. Requires LABS_MONGO_URI.
"""
from __future__ import annotations

import argparse
import sys


def _create(args) -> int:
    from ..core.security import create_key
    scopes = [s.strip() for s in (args.scopes or "analyze,read").split(",") if s.strip()]
    try:
        rec = create_key(name=args.name, owner=args.owner, scopes=scopes,
                         quota_per_day=args.quota, env=args.env)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\nAPI key created. Copy it now, it is not recoverable.\n")
    print(f"  key         {rec['key']}")
    print(f"  name        {rec['name']}")
    print(f"  fingerprint {rec['fingerprint']}")
    print(f"  scopes      {', '.join(rec['scopes'])}")
    print(f"  quota/day   {rec['quota_per_day'] or 'unlimited'}\n")
    print("Use it as:  -H 'X-API-Key: <key>'\n")
    return 0


def _list(_args) -> int:
    from ..core.security import list_keys
    rows = list_keys()
    if not rows:
        print("No keys. Create one with: labs-keys create --name ...")
        return 0
    print(f"\n{'FINGERPRINT':<28} {'NAME':<24} {'SCOPES':<20} "
          f"{'QUOTA':<10} {'ACTIVE':<7} LAST USED")
    print("-" * 108)
    for r in rows:
        print(f"{str(r.get('fingerprint', '')):<28} "
              f"{str(r.get('name', ''))[:23]:<24} "
              f"{','.join(r.get('scopes') or [])[:19]:<20} "
              f"{str(r.get('quota_per_day') or '-'):<10} "
              f"{str(bool(r.get('active'))):<7} "
              f"{r.get('last_used_at') or 'never'}")
    print()
    return 0


def _revoke(args) -> int:
    from ..core.security import revoke_key
    if revoke_key(args.fingerprint):
        print(f"Revoked {args.fingerprint}")
        return 0
    print(f"No active key with fingerprint {args.fingerprint}", file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="labs-keys", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="mint a new key")
    c.add_argument("--name", required=True)
    c.add_argument("--owner", default=None)
    c.add_argument("--scopes", default="analyze,read",
                   help="comma separated: analyze, read, admin")
    c.add_argument("--quota", type=int, default=0,
                   help="requests per day, 0 for unlimited")
    c.add_argument("--env", default="live", choices=["live", "test"])
    c.set_defaults(fn=_create)

    sub.add_parser("list", help="list keys").set_defaults(fn=_list)

    r = sub.add_parser("revoke", help="deactivate a key")
    r.add_argument("--fingerprint", required=True)
    r.set_defaults(fn=_revoke)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
