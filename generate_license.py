#!/usr/bin/env python3
"""Ashlr AO — License Key Generator (standalone tool, not part of package).

Subcommands:
  generate-keypair  Create a new Ed25519 private/public PEM key pair
  generate          Sign a JWT license key using the private key
  decode            Decode a JWT license key without verification (debug)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cmd_generate_keypair(args):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    priv_path = out / "license_private.pem"
    pub_path = out / "license_public.pem"

    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    print(f"Private key: {priv_path}")
    print(f"Public key:  {pub_path}")
    print()
    print("Public key PEM (embed in server.py LICENSE_PUBLIC_KEY_PEM):")
    print(pub_pem.decode())


def cmd_generate(args):
    import jwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    priv_path = Path(args.private_key)
    if not priv_path.exists():
        print(f"Error: private key not found at {priv_path}", file=sys.stderr)
        sys.exit(1)

    private_key = load_pem_private_key(priv_path.read_bytes(), password=None)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=args.days)

    features = ["multi_user", "intelligence", "workflows", "fleet_presets", "unlimited_agents"]

    payload = {
        "sub": args.org_id,
        "iss": "ashlr-licensing",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "tier": args.tier,
        "max_agents": args.max_agents,
        "max_seats": args.max_seats,
        "features": features,
    }

    token = jwt.encode(payload, private_key, algorithm="EdDSA")
    print(f"License key ({args.tier}, {args.days} days, max_agents={args.max_agents}):")
    print()
    print(token)
    print()
    print(f"Org ID:      {args.org_id}")
    print(f"Tier:        {args.tier}")
    print(f"Max agents:  {args.max_agents}")
    print(f"Max seats:   {args.max_seats}")
    print(f"Issued:      {now.isoformat()}")
    print(f"Expires:     {expires.isoformat()}")


def cmd_decode(args):
    import jwt

    try:
        payload = jwt.decode(args.token, options={"verify_signature": False})
        print(json.dumps(payload, indent=2, default=str))
    except Exception as e:
        print(f"Error decoding token: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Ashlr AO License Key Generator")
    sub = parser.add_subparsers(dest="command", required=True)

    # generate-keypair
    kp = sub.add_parser("generate-keypair", help="Generate Ed25519 key pair")
    kp.add_argument("--output-dir", default=".", help="Directory for PEM files (default: .)")

    # generate
    gen = sub.add_parser("generate", help="Generate a signed license key")
    gen.add_argument("--private-key", default="license_private.pem", help="Path to private key PEM")
    gen.add_argument("--org-id", required=True, help="Organization ID")
    gen.add_argument("--tier", default="pro", choices=["pro", "enterprise"], help="License tier")
    gen.add_argument("--max-agents", type=int, default=100, help="Max concurrent agents")
    gen.add_argument("--max-seats", type=int, default=50, help="Max users/seats")
    gen.add_argument("--days", type=int, default=365, help="Days until expiration")

    # decode
    dec = sub.add_parser("decode", help="Decode a JWT license key (no verification)")
    dec.add_argument("token", help="JWT license key string")

    args = parser.parse_args()
    if args.command == "generate-keypair":
        cmd_generate_keypair(args)
    elif args.command == "generate":
        cmd_generate(args)
    elif args.command == "decode":
        cmd_decode(args)


if __name__ == "__main__":
    main()
