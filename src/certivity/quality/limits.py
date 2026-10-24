"""Challenge output limits (from the Agent 3 brief)."""
MAX_TEXT_CHARS = 2_000
MAX_REQUIREMENTS = 5_000
MAX_OUTPUT_BYTES = 5 * 1024 * 1024

# Official public field names. ASSUMED: confirm against the participant package's
# schema (schemas/ in the supplied repo) before relying on them.
SUBMISSION_KEYS = ("team_id", "case_id", "requirements")
REQUIREMENT_KEYS = ("id", "text", "source")
SOURCE_KEYS = ("regulation", "version", "page", "paragraph")
