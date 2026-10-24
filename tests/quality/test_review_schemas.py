"""Provider rules for the schemas quality sends with each request.

A scripted fixture accepts any schema, so these rules only fail on a real call: an object left
open rejects every review call with HTTP 400, and the run reports every draft "Not reviewed".
"""
import json
import unittest

from certivity.quality.prompts.review_prompt import DEDUP_SCHEMA, REVIEW_SCHEMA

SCHEMAS = {"quality_review": REVIEW_SCHEMA, "quality_dedup": DEDUP_SCHEMA}
UNION_LIMIT = 16  # provider cap on union-typed parameters per schema


def objects(node, path="$"):
    """Every sub-schema declaring "type": "object", with its path."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield path, node
        for key, value in node.items():
            yield from objects(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from objects(value, f"{path}[{i}]")


def unions(node):
    if isinstance(node, dict):
        return (isinstance(node.get("type"), list)) + sum(unions(v) for v in node.values())
    if isinstance(node, list):
        return sum(unions(v) for v in node)
    return 0


class ReviewSchemas(unittest.TestCase):
    def test_every_object_closes_additional_properties(self):
        for name, schema in SCHEMAS.items():
            found = list(objects(schema))
            self.assertTrue(found, name)
            open_ = [path for path, node in found if node.get("additionalProperties") is not False]
            self.assertEqual(open_, [], f"{name}: objects without additionalProperties: False")

    def test_required_fields_are_defined(self):
        for name, schema in SCHEMAS.items():
            for path, node in objects(schema):
                missing = set(node.get("required", ())) - set(node.get("properties", {}))
                self.assertEqual(missing, set(), f"{name} {path}")

    def test_within_union_limit_and_serialisable(self):
        for name, schema in SCHEMAS.items():
            json.dumps(schema)
            self.assertLessEqual(unions(schema), UNION_LIMIT, name)


if __name__ == "__main__":
    unittest.main()
