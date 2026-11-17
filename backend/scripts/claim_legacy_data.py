"""Explicitly transfer pre-authentication rows to one named account.

Run after creating the destination account:
    python -m scripts.claim_legacy_data --email person@example.com

No legacy data is assigned automatically by application startup.
"""
import argparse
from app.services import database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Existing account to own unassigned legacy records")
    args = parser.parse_args()

    user = database.fetch_one(
        "SELECT id, email FROM users WHERE email = ? COLLATE NOCASE",
        (args.email.strip(),),
    )
    if not user:
        raise SystemExit("No account found for that email. Create the account first.")
    conversations, documents = database.claim_unowned_data(user["id"])

    print(f"Assigned {conversations} legacy conversations and {documents} legacy documents to {user['email']}.")


if __name__ == "__main__":
    main()
