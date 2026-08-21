from __future__ import annotations

import unittest

from autonomic_kb.markdown import dump_frontmatter, parse_frontmatter, parse_markdown, render_note


class MarkdownTests(unittest.TestCase):
    def test_frontmatter_round_trip_for_compact_schema(self) -> None:
        metadata = {
            "id": "kb:repository:fact:one",
            "title": "One",
            "confidence": 0.8,
            "applies_to": ["src/**", "tests/**"],
            "relations": {"depends-on": ["kb:repository:fact:two"]},
            "active": True,
        }
        text = dump_frontmatter(metadata) + "Body\n"
        parsed, body = parse_frontmatter(text)
        self.assertEqual(parsed["id"], metadata["id"])
        self.assertEqual(parsed["applies_to"], metadata["applies_to"])
        self.assertEqual(parsed["relations"], metadata["relations"])
        self.assertTrue(parsed["active"])
        self.assertEqual(body, "Body\n")

    def test_progressive_layers_links_and_tags(self) -> None:
        text = render_note(
            {"id": "kb:repository:fact:x", "summary": "Short fact", "tags": ["alpha"]},
            {0: "Pointer", 1: "Fact", 2: "Summary with [[Other Note]] and #beta", 3: "Detail"},
        )
        parsed = parse_markdown(text)
        self.assertEqual(parsed.layers[0], "Pointer")
        self.assertEqual(parsed.layers[2], "Summary with [[Other Note]] and #beta")
        self.assertEqual(parsed.links, ["Other Note"])
        self.assertEqual(parsed.tags, ["alpha", "beta"])

    def test_unlayered_markdown_gets_low_cost_fallbacks(self) -> None:
        parsed = parse_markdown("First paragraph.\n\nSecond paragraph.")
        self.assertIn("First paragraph", parsed.layers[1])
        self.assertIn("Second paragraph", parsed.layers[2])


if __name__ == "__main__":
    unittest.main()
