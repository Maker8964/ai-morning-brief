from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str  # badge label
    url: str
    weight: float = 1.0
    ai_only: bool = True  # False = general feed, keep only AI-related items
    kind: str = "rss"  # "rss" | "hn"


SOURCES: list[Source] = [
    Source("OpenAI", "https://openai.com/news/rss.xml", 1.4),
    Source("Google", "https://blog.google/technology/ai/rss/", 1.2),
    Source("DeepMind", "https://deepmind.google/blog/rss.xml", 1.3),
    Source("Hugging Face", "https://huggingface.co/blog/feed.xml", 1.0),
    Source("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 1.2),
    Source("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed", 1.2),
    Source("Ars Technica", "https://arstechnica.com/ai/feed/", 1.1),
    Source("Wired", "https://www.wired.com/feed/tag/ai/latest/rss", 1.0),
    Source("The Decoder", "https://the-decoder.com/feed/", 1.0),
    Source("TechCrunch", "https://techcrunch.com/feed/", 1.1, ai_only=False),
    Source("Bloomberg", "https://feeds.bloomberg.com/technology/news.rss", 1.3, ai_only=False),
    Source("HN", "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60", 1.0, ai_only=False, kind="hn"),
]
