"""Regenerate missing/legacy vectors in the active Gemini embedding space.

From the backend directory (or /app in the backend image):
    python -m scripts.reembed_gemini_embeddings --dry-run
    python -m scripts.reembed_gemini_embeddings

The job is resumable. Successful batches commit independently; a failed batch
is left pending and reported so a later run can retry it.
"""

import argparse
import logging
import sys

from app.services import database
from app.services.rag_service import generate_embeddings


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 64


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report vector counts only; do not apply schema changes or call Gemini.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Chunks fetched for each migration batch (1-100; default: 64).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.batch_size <= 100:
        logger.error("--batch-size must be between 1 and 100.")
        return 2

    if args.dry_run:
        # This is deliberately read-only: don't call init_db(), because that
        # applies the cutover migration and clears legacy vectors.
        counts = database.get_embedding_migration_counts()
        logger.info(
            "Dry run: total_chunks=%s ready=%s pending_reembedding=%s; "
            "no schema changes, vector writes, or Gemini calls performed.",
            counts["total"], counts["ready"], counts["pending"],
        )
        return 0

    database.init_db()
    initial = database.get_embedding_migration_counts()
    if initial["pending"] == 0:
        logger.info(
            "Embedding migration already complete: total_chunks=%s ready=%s pending=0.",
            initial["total"], initial["ready"],
        )
        return 0

    logger.info(
        "Starting Gemini re-embedding: total_chunks=%s ready=%s pending=%s batch_size=%s.",
        initial["total"], initial["ready"], initial["pending"], args.batch_size,
    )
    high_water = database.get_max_chunk_rowid()
    last_rowid = 0
    processed = 0
    failed_batches = 0
    batch_number = 0

    while True:
        rows = database.get_chunks_needing_embeddings(
            limit=args.batch_size,
            after_rowid=last_rowid,
            through_rowid=high_water,
        )
        if not rows:
            break

        batch_number += 1
        last_rowid = int(rows[-1]["_rowid"])
        try:
            vectors = generate_embeddings([row["content"] for row in rows])
            if len(vectors) != len(rows):
                raise RuntimeError("Embedding count did not match the selected chunk count.")
            changed = database.update_chunk_embeddings([
                (row["id"], vector.tobytes())
                for row, vector in zip(rows, vectors)
            ])
            if changed != len(rows):
                raise RuntimeError(
                    f"Updated {changed} of {len(rows)} selected chunks; check concurrent database changes."
                )
            processed += changed
        except Exception:
            failed_batches += 1
            logger.exception(
                "Batch %s failed for %s chunk(s); those rows remain eligible for a later run.",
                batch_number, len(rows),
            )

        logger.info(
            "Progress: batches=%s regenerated=%s/%s initial_pending=%s failed_batches=%s.",
            batch_number, processed, initial["pending"], initial["pending"], failed_batches,
        )

    final = database.get_embedding_migration_counts()
    if final["pending"]:
        logger.error(
            "Re-embedding incomplete: regenerated=%s remaining=%s failed_batches=%s. "
            "Rerun this command to resume.",
            processed, final["pending"], failed_batches,
        )
        return 1

    logger.info(
        "Gemini embedding migration complete: regenerated=%s remaining=0 total_chunks=%s.",
        processed, final["total"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
