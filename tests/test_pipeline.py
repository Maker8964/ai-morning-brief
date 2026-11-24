import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pipeline.brief import ShortBrief, build, main, story_to_card
from pipeline.dedup import dedup, normalize_url
from pipeline.ingest import is_ai, parse_rss
from pipeline.models import Item, Story
from pipeline.rank import rank
from pipeline.sources import Source
from pipeline.summarize import TOPIC_RULE, is_chinese, matches_story, parse_response, summarize

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

    def test_cap_counts_already_taken_stories(self):
        taken = [Story([item("Wired", f"taken {i}")]) for i in range(3)]
        stories = [Story([item("Wired", "w")]), Story([item("HN", "h", hours_ago=30)])]
        self.assertEqual([s.lead.source for s in rank(stories, now=NOW, taken=taken)], ["HN"])

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
        return [Story([item("Wired", "OpenAI ships GPT-5", summary="Excerpt one")]),
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
        self.assertEqual(stories[1].title, "x")  # title from the rejected reply is not kept
        self.assertTrue(all(is_chinese(s.summary) for s in stories))

    def test_english_source_text_is_translated(self):
        stories = self.stories()
        translated = "YC孵化的Tasklet正在招聘产品工程师和智能体基础设施工程师。"
        chat = FakeChat(reply([{"id": 0, "title": "One", "summary": ZH}]),  # story 1 missing
                        reply([]),                                         # retry returns nothing
                        reply([{"id": 0, "summary": translated}]))         # translate call
        self.run_summarize(stories, chat)
        self.assertEqual(chat.calls[2][1], [{"id": 0, "headline": stories[1].lead.title, "text": stories[1].lead.title}])
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
        self.assertIn("dropping story without a valid Chinese summary: Tasklet", logs)

    def test_without_key_english_stories_dropped_chinese_kept(self):
        stories = self.stories() + [Story([item("36Kr", "标题", summary=ZH)])]
        used, logs = self.run_summarize(stories, chat=None, key=None)
        self.assertFalse(used)
        self.assertEqual([(s.title, s.summary) for s in stories], [("标题", ZH)])
        self.assertIn("dropping story", logs)

    def test_reordered_reply_is_matched_by_id(self):
        stories = self.stories()
        tasklet = "YC孵化的Tasklet正在招聘产品与智能体基础设施工程师，扩充团队以加速研发。"
        chat = FakeChat(reply([{"id": 1, "title": "Tasklet hiring", "summary": tasklet},
                               {"id": 0, "title": "GPT-5 ships", "summary": ZH}]))
        self.run_summarize(stories, chat)
        self.assertEqual([(s.title, s.summary) for s in stories],
                         [("GPT-5 ships", ZH), ("Tasklet hiring", tasklet)])
        self.assertEqual(len(chat.calls), 1)

    def test_parse_response_accepts_fenced_json(self):
        rows = parse_response('```json\n{"stories":[{"id":"1","title":"T","summary":"S"}]}\n```')
        self.assertEqual(rows, {1: {"title": "T", "summary": "S"}})


RBC_TITLE = "RBC Has Ideas For Canada to Unlock Tech GDP Gains as NVIDIA Rallies"
RBC_ZH = "RBC提出加拿大释放科技行业GDP增长潜力的建议，同时NVIDIA股价继续上涨，带动科技板块走强。"
COP_ZH = "最新报道分析了此次COP气候大会谈判陷入僵局并最终走向失败的核心原因，引发国际社会广泛关注。"


class TopicTest(unittest.TestCase):
    stories, run_summarize = SummarizeTest.stories, SummarizeTest.run_summarize

    def rbc(self, excerpt="RBC economists say Canada could add billions to GDP from tech."):
        return Story([item("Bloomberg", RBC_TITLE, summary=excerpt)])

    def test_matches_story(self):
        self.assertFalse(matches_story(self.rbc(), COP_ZH))
        self.assertTrue(matches_story(self.rbc(), RBC_ZH))
        self.assertTrue(matches_story(self.rbc(), "英伟达股价上涨，加拿大科技行业有望带动经济增长。"))  # no Latin keywords
        self.assertTrue(matches_story(self.rbc(), "皇家银行经济学家Smith表示加拿大科技产业潜力巨大。".replace(
            "Smith", "economists")))  # keyword only in an on-topic excerpt
        self.assertFalse(matches_story(self.rbc("COP climate talks stall in Belem."), COP_ZH))  # off-topic excerpt
        self.assertTrue(matches_story(Story([item("36Kr", "标题", summary=ZH)]), ZH))  # Chinese-only headline

    def test_off_topic_summary_retried_with_headline_and_fixed(self):
        stories = [self.rbc()]
        chat = FakeChat(reply([{"id": 0, "title": "RBC Canada tech GDP", "summary": COP_ZH}]),
                        reply([{"id": 0, "title": "RBC Canada tech GDP", "summary": RBC_ZH}]))
        _, logs = self.run_summarize(stories, chat)
        self.assertEqual(stories[0].summary, RBC_ZH)
        self.assertIn(TOPIC_RULE, chat.calls[1][0])
        sent = chat.calls[1][1][0]["sources"][0]
        self.assertEqual((sent["headline"], sent["excerpt"]), (RBC_TITLE, stories[0].lead.summary))
        self.assertIn("off-topic", logs)

    def test_unrelated_llm_title_cannot_validate_unrelated_summary(self):
        stories = [self.rbc()]
        chat = FakeChat(reply([{"id": 0, "title": "COP climate talks stall", "summary": COP_ZH}]),
                        reply([{"id": 0, "title": "RBC Canada tech GDP", "summary": RBC_ZH}]))
        self.run_summarize(stories, chat)
        self.assertEqual((stories[0].title, stories[0].summary), ("RBC Canada tech GDP", RBC_ZH))

    def test_off_topic_summary_dropped_after_retry(self):
        stories = [self.rbc(), self.stories()[0]]
        chat = FakeChat(reply([{"id": 0, "title": "RBC Canada", "summary": COP_ZH},
                               {"id": 1, "title": "GPT-5 ships", "summary": ZH}]),
                        reply([{"id": 0, "title": "RBC Canada", "summary": COP_ZH}]),
                        reply([{"id": 0, "summary": COP_ZH}]))  # translate call
        _, logs = self.run_summarize(stories, chat)
        self.assertEqual([(s.title, s.summary) for s in stories], [("GPT-5 ships", ZH)])  # untouched
        self.assertEqual([p["id"] for p in chat.calls[1][1]], [0])
        self.assertIn("dropping story without a valid Chinese summary: RBC", logs)


def stories_for(n, offset=0, sources=None):
    words = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet",
             "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo", "Sierra", "Tango"]
    return [item(f"S{i % sources if sources else i}", f"{words[i]}corp unveils {words[i]}bot model", hours_ago=1 + i)
            for i in range(offset, offset + n)]


def fake_summarize(drop=()):
    def run(batch):
        for s in batch:
            s.title = s.lead.title
            s.summary = s.lead.title.split()[0] + "发布了新的人工智能模型，在多项基准测试中取得领先成绩。"
        batch[:] = [s for s in batch if s.title.split("corp")[0] not in drop]
        return True
    return run


class BriefTest(unittest.TestCase):
    def build(self, by_window, drop=()):
        ingest = mock.Mock(side_effect=lambda now, window_hours: by_window[window_hours])
        with mock.patch("pipeline.brief.ingest", ingest), \
                mock.patch("pipeline.brief.summarize", side_effect=fake_summarize(drop)):
            return build(now=NOW), [c.kwargs["window_hours"] for c in ingest.call_args_list]

    def test_full_brief_uses_one_window(self):
        brief, windows = self.build({36: stories_for(14)})
        self.assertEqual((len(brief["stories"]), windows), (12, [36]))

    def test_dropped_stories_backfilled_from_next_ranked(self):
        brief, windows = self.build({36: stories_for(14)}, drop=("Alpha", "Bravo"))
        titles = [c["title"] for c in brief["stories"]]
        self.assertEqual((len(titles), windows), (12, [36]))
        self.assertIn("Mikecorp unveils Mikebot model", titles)
        self.assertNotIn("Alphacorp unveils Alphabot model", titles)

    def test_backfill_reaches_source_whose_story_was_rejected(self):
        # 16 stories from 4 sources: the per-source cap of 3 leaves Mike (S0's 4th) out at first.
        brief, windows = self.build({36: stories_for(16, sources=4)}, drop=("Alpha",))
        titles = [c["title"] for c in brief["stories"]]
        self.assertEqual((len(titles), windows), (12, [36]))
        self.assertIn("Mikecorp unveils Mikebot model", titles)

    def test_short_window_retried_with_wider_window(self):
        brief, windows = self.build({36: stories_for(8), 48: stories_for(14)})
        self.assertEqual((len(brief["stories"]), windows), (12, [36, 48]))
        self.assertEqual(len({c["id"] for c in brief["stories"]}), 12)

    def test_still_short_fails_without_overwriting_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = f"{tmp}/brief.json"
            with open(out, "w") as f:
                f.write("previous")
            ingest = mock.Mock(side_effect=lambda now, window_hours: stories_for(10))
            with mock.patch("pipeline.brief.ingest", ingest), \
                    mock.patch("pipeline.brief.summarize", side_effect=fake_summarize(("Alpha",))), \
                    mock.patch("sys.argv", ["brief", "--out", out]), \
                    self.assertLogs("pipeline.brief") as logs, self.assertRaises(SystemExit) as exit:
                main()
            with open(out) as f:
                self.assertEqual(f.read(), "previous")
        self.assertEqual(exit.exception.code, 1)
        log = "\n".join(logs.output)
        self.assertIn("retrying with a 48h window", log)
        self.assertIn("brief short: 9/12 stories, ingest/dedup/rank stage came up short", log)
        self.assertIn("window=36h items=10 stories=10 attempted=10 kept=9", log)

    def test_summarize_shortfall_names_summarize_stage(self):
        with self.assertRaises(ShortBrief) as err:
            self.build({36: stories_for(13), 48: stories_for(13)},
                       drop=("Alpha", "Bravo", "Charlie", "Delta"))
        self.assertIn("9/12 stories, summarize stage", str(err.exception))


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
