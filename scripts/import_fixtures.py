"""``make import`` entry point.

Calls the same code as ``POST /api/v1/admin/reimport`` will eventually call
(reimport endpoint not yet wired in this slice). Output goes through
``api.logging`` so the import's start/commit/abort events are
JSON-formatted alongside the runner's own structured logs.
"""
from __future__ import annotations

import sys

from api.importer.runner import FixtureImportError, import_fixtures
from api.logging import get_logger
from api.settings import get_settings


_logger = get_logger("bible_study.importer.cli")


def main() -> int:
    settings = get_settings()
    try:
        result = import_fixtures(project_root=settings.project_root)
    except FixtureImportError as exc:
        _logger.error(
            "import.cli.failed",
            extra={
                "error_code": exc.code,
                "error_message": exc.message,
                "details": exc.details,
            },
        )
        return 1

    _logger.info(
        "import.cli.completed",
        extra={
            "fixture_version": result.fixture_version,
            "files_imported": result.files_imported,
            "sentences_built": result.sentences_built,
            "words_built": result.words_built,
            "red_letter_source_ranges": result.red_letter_source_ranges,
            "elapsed_ms": result.took_ms,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
