from typing import List

import matplotlib.pyplot as plt
import pandas as pd
import requests
from wordcloud import STOPWORDS, WordCloud

from dagster import Field, MetadataValue, OpExecutionContext, asset, file_relative_path


@asset(group_name="hackernews", compute_kind="HackerNews API")
def hackernews_topstory_ids() -> List[int]:
    """
    Get up to 500 top stories

    Docs: https://github.com/HackerNews/API#new-top-and-best-stories
    """
    newstories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    top_500_newstories = requests.get(newstories_url).json()
    return top_500_newstories


@asset(
    group_name="hackernews",
    compute_kind="HackerNews API",
    config_schema={"sample_size": Field(int, is_required=False)},
)
def hackernews_topstories(
    context: OpExecutionContext, hackernews_topstory_ids: List[int]
) -> pd.DataFrame:
    """
    Get items based on story ids. Default to fetching all (up to 500 items) which may take longer.

    Docs: https://github.com/HackerNews/API#items
    """
    sample_size = context.op_config.get("sample_size", len(hackernews_topstory_ids))
    context.log.info(
        f"Fetching {sample_size} items. You can change the sample size in config. TODO: add doc link."
    )

    results = []

    for item_id in hackernews_topstory_ids[:sample_size]:
        item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json").json()
        results.append(item)
        if len(results) % 20 == 0:
            context.log.info(f"Got {len(results)} items so far.")

    df = pd.DataFrame(results)
    context.add_output_metadata(
        {
            "num_records": len(df),
            "preview": MetadataValue.md(df.head().to_markdown()),
        }
    )
    return df


@asset(group_name="hackernews", compute_kind="Plot")
def hackernews_topstories_word_cloud(
    context: OpExecutionContext, hackernews_topstories: pd.DataFrame
):
    """
    Exploratory analysis: Generate a word cloud from the current top 500 HN top stories.

    Docs: TODO word cloud doc link
    """
    stopwords = set(STOPWORDS)
    stopwords.update(["Ask", "Show", "HN"])
    titles_text = " ".join(hackernews_topstories["title"])
    titles_cloud = WordCloud(stopwords=stopwords, background_color="white").generate(titles_text)
    plt.figure(figsize=(8, 8), facecolor=None)
    plt.imshow(titles_cloud, interpolation="bilinear")
    plt.axis("off")
    plt.tight_layout(pad=0)

    filepath = file_relative_path(__file__, "hackernews_word_cloud.png")
    plt.savefig(filepath)
    context.add_output_metadata({"plot_path": MetadataValue.path(filepath)})


# TODO: do we want the part below? this does the daily agg as the github asset group does. and then we
# merge two DFs by date and build a "daily report". but not including it can make the quickstart
# simpler.

# @asset(
#     group_name="hackernews",
#     compute_kind="Pandas",
#     config_schema={"keyword": Field(str, description="by default. no keyword", is_required=False)},
# )
# def hackernews_stories_by_date(
#     context: OpExecutionContext, hackernews_topstories: pd.DataFrame
# ) -> pd.DataFrame:
#     """
#     Aggregate stories by date. Filter stories based on the given keyword.

#     No keyword filtering by default. You can change the keyword by supplying the config. TODO: link to config docs
#     """

#     df = pd.DataFrame(hackernews_topstories)

#     keyword = context.op_config.get("keyword")
#     if keyword:
#         df = df[df["title"].str.contains(keyword, case=False, na=False)]

#     df["date"] = pd.to_datetime(df["time"], unit="s").dt.date

#     result = (
#         df["date"]
#         .groupby(by=df["date"])
#         .count()
#         .reset_index(name="num_hn_stories")
#         .sort_values("date")
#     )
#     context.add_output_metadata(
#         {"preview": MetadataValue.md(result.head().to_markdown()), "keyword": keyword}
#     )

#     return result
