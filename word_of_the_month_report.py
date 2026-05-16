from collections import Counter
import re

import pandas as pd
import matplotlib.pyplot as plt
import plotly.express as px
from wordcloud import WordCloud, STOPWORDS
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "had", "he", "her", "hers", "him", "his", "i", "in",
    "is", "it", "its", "me", "my", "of", "on", "or", "our", "ours",
    "she", "that", "the", "their", "them", "there", "these", "they",
    "this", "to", "was", "we", "were", "will", "with", "you", "your",
    "ticket", "issue", "jira", "done", "need", "please", "thanks",
    "thank", "update", "updated", "work", "task", "story", "project",
    "deploy", "deployment", "error", "errors", "fix", "fixed",
}


_SENTIMENT_ANALYZER = SentimentIntensityAnalyzer()


def _empty_payload() -> dict:
    return {
        "bar_fig": None,
        "treemap_fig": None,
        "wordcloud_fig": None,
        "sentiment_fig": None,
        "summary_df": pd.DataFrame(),
        "sentiment_df": pd.DataFrame(),
        "top_word": None,
        "top_frequency": 0,
        "start_month": None,
        "end_month": None,
        "available_months": [],
        "error_message": None,
    }


def _ensure_month_column(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "month" in out.columns:
        out["month"] = out["month"].astype(str)
        return out

    if {"year_created", "month_created"}.issubset(out.columns):
        out["month"] = (
            out["year_created"].astype("Int64").astype(str)
            + "-"
            + out["month_created"].astype("Int64").astype(str).str.zfill(2)
        )
        return out

    if "created" in out.columns:
        created = pd.to_datetime(out["created"], errors="coerce")
        out["month"] = created.dt.to_period("M").astype(str)
        return out

    out["month"] = pd.NA
    return out


def _month_bounds(available_months: list[str]) -> tuple[str | None, str | None]:
    if not available_months:
        return None, None

    current_month = pd.Timestamp.today().to_period("M")
    default_start = (current_month - 2).strftime("%Y-%m")
    default_end = current_month.strftime("%Y-%m")

    if default_end < available_months[0]:
        return available_months[0], available_months[min(2, len(available_months) - 1)]

    start_month = max(default_start, available_months[0])
    end_month = min(default_end, available_months[-1])

    if start_month > end_month:
        start_month = available_months[max(0, len(available_months) - 3)]
        end_month = available_months[-1]

    return start_month, end_month


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _tokenize_summary(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z']+", str(text).lower())
    return [word for word in words if len(word) > 2 and word not in _STOPWORDS]


def _tokenize_comment_text(text: str) -> str:
    words = re.findall(r"[a-zA-Z][a-zA-Z']+", str(text).lower())
    return " ".join(word for word in words if len(word) > 2 and word not in _STOPWORDS)


def _sentiment_bucket(compound: float) -> str:
    if compound >= 0.05:
        return "Positive"
    if compound <= -0.05:
        return "Negative"
    return "Neutral"


def _sentiment_color(word: str, **kwargs) -> str:
    score = _SENTIMENT_ANALYZER.lexicon.get(word.lower(), 0)
    if score > 1.5:
        return "#16a34a"
    if score > 0:
        return "#65a30d"
    if score < -1.5:
        return "#dc2626"
    if score < 0:
        return "#f97316"
    return "#64748b"


def build_word_of_the_month_visuals(
    df_issues: pd.DataFrame,
    start_month: str | None = None,
    end_month: str | None = None,
    top_n: int = 15,
) -> dict:
    payload = _empty_payload()

    if df_issues is None or df_issues.empty:
        payload["error_message"] = "No ticket data available."
        return payload

    df = _ensure_month_column(df_issues)
    if "summary" not in df.columns:
        payload["error_message"] = "Summary column not found in data."
        return payload

    comment_col = _first_existing_column(
        df,
        [
            "comment_body",
            "comments",
            "comment_text",
            "comment",
            "description",
            "summary",
        ],
    )

    df["summary"] = df["summary"].fillna("").astype(str)
    df["month"] = df["month"].fillna("").astype(str)
    df = df[df["month"].str.match(r"^\d{4}-\d{2}$", na=False)].copy()

    if df.empty:
        payload["error_message"] = "No valid month data available."
        return payload

    available_months = sorted(df["month"].dropna().unique().tolist())
    payload["available_months"] = available_months

    default_start, default_end = _month_bounds(available_months)
    start = start_month or default_start
    end = end_month or default_end

    if start is None or end is None:
        payload["error_message"] = "Could not determine month range."
        return payload

    if start > end:
        start, end = end, start

    payload["start_month"] = start
    payload["end_month"] = end

    filtered = df[(df["month"] >= start) & (df["month"] <= end)].copy()
    if filtered.empty:
        payload["error_message"] = f"No tickets found between {start} and {end}."
        return payload

    # Sentiment-aware comment analysis prefers comment text, then description, then summary.
    sentiment_source = filtered[comment_col].fillna("").astype(str) if comment_col is not None else filtered["summary"].fillna("").astype(str)
    comment_texts = sentiment_source.map(_tokenize_comment_text)
    comment_texts = comment_texts[comment_texts.str.strip() != ""]

    sentiment_records = []
    for text in comment_texts:
        compound = _SENTIMENT_ANALYZER.polarity_scores(text)["compound"]
        sentiment_records.append(
            {
                "text": text,
                "compound": compound,
                "bucket": _sentiment_bucket(compound),
            }
        )

    sentiment_df = pd.DataFrame(sentiment_records)
    if not sentiment_df.empty:
        sentiment_counts = sentiment_df["bucket"].value_counts().reindex(["Positive", "Neutral", "Negative"], fill_value=0).reset_index()
        sentiment_counts.columns = ["Sentiment", "Count"]
    else:
        sentiment_counts = pd.DataFrame({"Sentiment": ["Positive", "Neutral", "Negative"], "Count": [0, 0, 0]})

    all_words: list[str] = []
    for summary in filtered["summary"]:
        all_words.extend(_tokenize_summary(summary))

    word_counts = Counter(all_words)
    if not word_counts:
        payload["error_message"] = "No usable words found in ticket summaries for the selected range."
        return payload

    word_df = (
        pd.DataFrame(word_counts.most_common(top_n), columns=["word", "frequency"])
        .assign(percent=lambda d: (d["frequency"] / d["frequency"].sum() * 100).round(1))
    )

    bar_fig = px.bar(
        word_df.sort_values("frequency", ascending=True),
        x="frequency",
        y="word",
        orientation="h",
        title=f"Top Keywords in Ticket Summaries ({start} to {end})",
        color="frequency",
        color_continuous_scale="Teal",
        text="frequency",
    )
    bar_fig.update_layout(height=450, xaxis_title="Frequency", yaxis_title="Word")

    treemap_fig = px.treemap(
        word_df,
        path=["word"],
        values="frequency",
        title=f"Keyword Treemap ({start} to {end})",
        color="frequency",
        color_continuous_scale="Purples",
    )
    treemap_fig.update_layout(height=450)

    # Comment sentiment word cloud (or summary fallback if no comments exist).
    sentiment_text = " ".join(comment_texts.tolist())
    if not sentiment_text.strip():
        sentiment_text = " ".join(_tokenize_comment_text(text) for text in filtered["summary"].astype(str).tolist())

    wordcloud_fig = None
    sentiment_fig = None
    if sentiment_text.strip():
        wc = WordCloud(
            width=1200,
            height=700,
            background_color="#ffffff",
            colormap="RdYlGn",
            stopwords=set(STOPWORDS).union(_STOPWORDS),
            collocations=False,
            prefer_horizontal=0.9,
            max_words=180,
        ).generate(sentiment_text)

        fig, ax = plt.subplots(figsize=(14, 7))
        ax.imshow(wc.recolor(color_func=_sentiment_color), interpolation="bilinear")
        ax.axis("off")
        ax.set_title(f"Sentiment-Aware Comment Word Cloud ({start} to {end})", fontsize=18, fontweight="bold")
        fig.tight_layout(pad=0.2)
        wordcloud_fig = fig

        sentiment_fig = px.pie(
            sentiment_counts,
            names="Sentiment",
            values="Count",
            hole=0.5,
            title=f"Comment Sentiment Distribution ({start} to {end})",
            color="Sentiment",
            color_discrete_map={"Positive": "#16a34a", "Neutral": "#64748b", "Negative": "#dc2626"},
        )
        sentiment_fig.update_layout(height=380)

    payload["bar_fig"] = bar_fig
    payload["treemap_fig"] = treemap_fig
    payload["wordcloud_fig"] = wordcloud_fig
    payload["sentiment_fig"] = sentiment_fig
    payload["summary_df"] = word_df.rename(columns={"word": "Word", "frequency": "Frequency", "percent": "Percent"})
    payload["top_word"] = word_df.iloc[0]["word"]
    payload["top_frequency"] = int(word_df.iloc[0]["frequency"])
    payload["sentiment_df"] = sentiment_df

    return payload
