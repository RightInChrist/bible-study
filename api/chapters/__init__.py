"""Chapter-summary feature (Slice 8).

Read-side endpoints for chapter summaries plus the service layer the
static-site builder calls directly. Writes go through the existing
``POST /api/v1/runs`` endpoint with ``scope.kind == 'chapter_summary'``.
"""
