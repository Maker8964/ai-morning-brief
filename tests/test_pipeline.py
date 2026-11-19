import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pipeline.brief import story_to_card
from pipeline.dedup import dedup, normalize_url
from pipeline.ingest import is_ai, parse_rss
from pipeline.models import Item, Story
from pipeline.rank import rank
from pipeline.sources import Source
from pipeline.summarize import parse_response, summarize

NOW = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)


def item(source, title, url=None, hours_ago=1, weight=1.0, summary="", points=0):
    return Item(source, title, url or f"https://{source.lower().replace(' ', '')}.com/{abs(hash(title))}",
                NOW - timedelta(hours=hours_ago), summary, weight, points)


class NormalizeUrlTest(unittest.TestCase):
    def test_strips_tracking_www_and_trailing_slash(self):
        self.assertEqual(
            normalize_url("http://www.example.com/a/b/?utm_source=x&id=3&fbclid=y"),
            normalize_url("https://example.com/a/b?id=3"),
        )


class DedupTest(unittest.TestCase):
    def test_merges_same_story_across_sources(self):
        items = [
            item("TechCrunch", "Anthropic prices IPO at $83 a share, above projected range"),
            item("HN", "Anthropic prices initial public offering"),
            item("The Verge", "Will Wright's next game uses AI to map your memories"),
            item("Ars Technica", "Will Wright's next simulation will use AI to mine content from your memories"),
            item("Wired", "Nvidia unveils new robotics chip for factories"),
            item("Bloomberg", "Apple delays smart home hub again"),
        ]
        groups = sorted(sorted(i.source for i in s.items) for s in dedup(items))
        self.assertEqual(groups, [["Ars Technica", "The Verge"], ["Bloomberg"], ["HN", "TechCrunch"], ["Wired"]])

    def test_same_url_merges_even_with_different_titles(self):
        a = item("HN", "Show HN: something", url="https://example.com/post?utm_source=hn")
        b = item("The Decoder", "Totally different headline", url="https://www.example.com/post/")
        self.assertEqual(len(dedup([a, b])), 1)

    def test_unrelated_stories_sharing_one_name_stay_apart(self):
        items = [
            item("Wired", "OpenAI launches new image model for designers"),
            item("The Verge", "OpenAI board adds two new members"),
        ]
        self.assertEqual(len(dedup(items)), 2)


class RankTest(unittest.TestCase):
    def test_multi_source_story_outranks_single_source(self):
        multi = Story([item("Wired", "A"), item("The Verge", "A2")])
        single = Story([item("Wired", "B")])
        self.assertEqual(rank([single, multi], now=NOW)[0], multi)

    def test_newer_beats_older_at_equal_authority(self):
        old = Story([item("Wired", "old", hours_ago=30)])
        new = Story([item("Wired", "new", hours_ago=1)])
        self.assertEqual(rank([old, new], now=NOW)[0], new)

    def test_caps_stories_per_lead_source(self):
        stories = [Story([item("Bloomberg", f"s{i}", hours_ago=i)]) for i in range(6)]
        stories.append(Story([item("Wired", "w", hours_ago=10)]))
        picked = rank(stories, now=NOW, limit=5, per_source=3)
        self.assertEqual([s.sources[0] for s in picked].count("Bloomberg"), 3)
        self.assertIn("Wired", [s.sources[0] for s in picked])

    def test_cap_counts_lead_item_source(self):
        # Lead (items[0]) is HN even though Bloomberg has higher weight.
        stories = [Story([item("HN", f"h{i}", hours_ago=i), item("Bloomberg", f"b{i}", weight=1.3)]) for i in range(5)]
        self.assertEqual(len(rank(stories, now=NOW, per_source=3)), 3)


class IngestTest(unittest.TestCase):
    RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
      <item><title>Model &amp; chip news</title><link>https://ex.com/1</link>
        <pubDate>Wed, 18 Nov 2026 22:00:00 GMT</pubDate><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
      <item><title>No date</title><link>https://ex.com/2</link></item>
    </channel></rss>"""

    def test_parse_rss_cleans_html_and_skips_undated(self):
        items = parse_rss(Source("X", "u"), self.RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Model & chip news")
        self.assertEqual(items[0].summary, "Hello world")

    def test_malformed_feed_is_skipped(self):
        from pipeline.ingest import fetch_source
        with mock.patch("pipeline.ingest._fetch", return_value=b"not json"):
            self.assertEqual(fetch_source(Source("HN", "u", kind="hn")), [])

    def test_ai_filter(self):
        self.assertTrue(is_ai("OpenAI ships GPT-5.1"))
        self.assertTrue(is_ai("Why AI agents fail"))
        self.assertFalse(is_ai("Apple delays smart home hub"))
        self.assertFalse(is_ai("Said the chairman"))  # 'ai' inside a word


class SummarizeTest(unittest.TestCase):
    def stories(self):
        return [Story([item("Wired", "Original headline one", summary="Excerpt one")]),
                Story([item("HN", "Original headline two")])]

    def test_fallback_without_key(self):
        stories = self.stories()
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(summarize(stories))
        self.assertEqual(stories[0].title, "Original headline one")
        self.assertEqual(stories[0].summary, "Excerpt one")
        self.assertEqual(stories[1].summary, "Original headline two")

    def test_llm_result_applied_and_missing_rows_fall_back(self):
        stories = self.stories()
        reply = json.dumps({"stories": [{"id": 0, "title": "Punchy Title.", "summary": "中文摘要。"}]}, ensure_ascii=False)
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}):
            self.assertTrue(summarize(stories, chat=lambda *a: reply))
        self.assertEqual((stories[0].title, stories[0].summary), ("Punchy Title", "中文摘要。"))
        self.assertEqual(stories[1].title, "Original headline two")

    def test_llm_error_falls_back(self):
        def boom(*a):
            raise TimeoutError
        stories = self.stories()
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}):
            self.assertFalse(summarize(stories, chat=boom))
        self.assertEqual(stories[0].title, "Original headline one")

    def test_parse_response_accepts_fenced_json(self):
        rows = parse_response('```json\n{"stories":[{"id":"1","title":"T","summary":"S"}]}\n```')
        self.assertEqual(rows, {1: {"title": "T", "summary": "S"}})


class CardTest(unittest.TestCase):
    def test_card_shape_and_badge_limit(self):
        s = Story([item("Wired", "a", weight=1.0), item("Bloomberg", "b", weight=1.3), item("HN", "c", weight=0.5)],
                  score=1.23456, title="T", summary="S")
        card = story_to_card(s)
        self.assertEqual(set(card), {"id", "title", "summary", "sources", "publishedAt", "url", "score"})
        self.assertEqual(card["sources"], ["Bloomberg", "Wired"])
        self.assertTrue(card["publishedAt"].endswith("+08:00"))


if __name__ == "__main__":
    unittest.main()
