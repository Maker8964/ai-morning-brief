import json
import logging
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pipeline.brief import story_to_card
from pipeline.dedup import dedup, normalize_url
from pipeline.ingest import is_ai, parse_rss
from pipeline.models import Item, Story
from pipeline.rank import rank
from pipeline.sources import Source
from pipeline.summarize import is_chinese, parse_response, summarize

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

    def test_zero_limit_selects_nothing(self):
        self.assertEqual(rank([Story([item("Wired", "a")])], now=NOW, limit=0), [])

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


ZH = "OpenAI发布了GPT-5，在推理和编程基准上大幅领先上一代模型，并向所有付费用户开放。"


def reply(rows):
    return json.dumps({"stories": rows}, ensure_ascii=False)


class FakeChat:
    """Answers each call with the next canned reply and records the system prompts and payloads."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages, *config):
        self.calls.append((messages[0]["content"], json.loads(messages[1]["content"])))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class SummarizeTest(unittest.TestCase):
    def stories(self):
        return [Story([item("Wired", "Original headline one", summary="Excerpt one")]),
                Story([item("HN", "Tasklet (YC P26) Is Hiring Product and Agent Infra Engineers")])]

    def run_summarize(self, stories, chat, key="k"):
        env = {"LLM_API_KEY": key} if key else {}
        with mock.patch.dict("os.environ", env, clear=True), self.assertLogs("pipeline.summarize", "DEBUG") as logs:
            logging.getLogger("pipeline.summarize").debug("start")
            used = summarize(stories, chat=chat)
        return used, "\n".join(logs.output)

    def test_is_chinese(self):
        self.assertTrue(is_chinese(ZH))
        self.assertTrue(is_chinese("Anthropic推出Claude新版本。"))
        self.assertFalse(is_chinese("Tasklet (YC P26) Is Hiring Product and Agent Infra Engineers"))
        self.assertFalse(is_chinese("Chess.com is ready to go a lot bigger than chess. 国际象棋"))
        self.assertFalse(is_chinese(""))
        self.assertFalse(is_chinese("The company said " * 10 + "这是一个非常重要的人工智能领域的突破性进展消息"))

    def test_chinese_summary_passes_through_untouched(self):
        stories = self.stories()[:1]
        chat = FakeChat(reply([{"id": 0, "title": "Punchy Title.", "summary": ZH}]))
        self.assertTrue(self.run_summarize(stories, chat)[0])
        self.assertEqual((stories[0].title, stories[0].summary), ("Punchy Title", ZH))
        self.assertEqual(len(chat.calls), 1)

    def test_english_summary_retried_once_with_stronger_prompt(self):
        stories = self.stories()
        chat = FakeChat(
            reply([{"id": 0, "title": "One", "summary": ZH},
                   {"id": 1, "title": "Tasklet hiring", "summary": "Tasklet Is Hiring Engineers"}]),
            reply([{"id": 1, "title": "x", "summary": "YC孵化的Tasklet正在招聘产品与智能体基础设施工程师，扩充团队以加速其AI代理产品的研发。"}]),
        )
        self.run_summarize(stories, chat)
        self.assertEqual([p["id"] for p in chat.calls[1][1]], [1])
        self.assertIn("MUST be Simplified Chinese", chat.calls[1][0])
        self.assertEqual(stories[1].title, "Tasklet hiring")
        self.assertTrue(all(is_chinese(s.summary) for s in stories))

    def test_english_source_text_is_translated(self):
        stories = self.stories()
        translated = "YC孵化的Tasklet正在招聘产品工程师和智能体基础设施工程师。"
        chat = FakeChat(reply([{"id": 0, "title": "One", "summary": ZH}]),  # story 1 missing
                        reply([]),                                         # retry returns nothing
                        reply([{"id": 0, "summary": translated}]))         # translate call
        self.run_summarize(stories, chat)
        self.assertEqual(chat.calls[2][1], [{"id": 0, "text": stories[1].lead.title}])
        self.assertEqual(stories[1].summary, translated)
        self.assertEqual(stories[1].title, stories[1].lead.title)

    def test_story_dropped_when_translation_fails(self):
        stories = self.stories()
        chat = FakeChat(reply([{"id": 0, "title": "One", "summary": ZH},
                               {"id": 1, "title": "Two", "summary": "Still English"}]),
                        TimeoutError(),
                        reply([{"id": 0, "summary": "Still English after translation"}]))
        _, logs = self.run_summarize(stories, chat)
        self.assertEqual([s.summary for s in stories], [ZH])
        self.assertIn("dropping story without a Chinese summary: Tasklet", logs)

    def test_without_key_english_stories_dropped_chinese_kept(self):
        stories = self.stories() + [Story([item("36Kr", "标题", summary=ZH)])]
        used, logs = self.run_summarize(stories, chat=None, key=None)
        self.assertFalse(used)
        self.assertEqual([(s.title, s.summary) for s in stories], [("标题", ZH)])
        self.assertIn("dropping story", logs)

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
