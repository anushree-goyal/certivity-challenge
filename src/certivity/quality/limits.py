"""Challenge output limits (from the Agent 3 brief)."""
MAX_TEXT_CHARS = 2_000
MAX_REQUIREMENTS = 5_000
MAX_OUTPUT_BYTES = 5 * 1024 * 1024

# Official public field names, checked against schemas/submission_main.schema.json
# (package commit 2104a8e). source.paragraph is optional but must be a string when present.
SUBMISSION_KEYS = ("team_id", "case_id", "requirements")
REQUIREMENT_KEYS = ("id", "text", "source")
SOURCE_KEYS = ("regulation", "version", "page", "paragraph")
