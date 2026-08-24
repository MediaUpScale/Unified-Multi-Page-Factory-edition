# -*- coding: utf-8 -*-
"""
Principles of Wealth — Finance & Economics.

Library-ingest channel: existing long-form episodes + Shorts live on the
external production drive. The factory only needs read access to the source
directory; processed (re-signed) files land in a Processed/ sibling folder.

Entry point: ``python wealth_main.py``
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Core profile
# ---------------------------------------------------------------------------
PAGE_ID: str = "principles_of_wealth_finance_economics"
PAGE_DISPLAY_NAME: str = "Principles of Wealth — Finance & Economics"
CONTENT_NICHE: str = "Educational / Financial Curation"
CONTENT_TYPE: str = "Educational / Financial Curation"

DEFAULT_AVATAR_MODE: str = "OFF"
DEFAULT_FORMAT: str = "REFERENCE_BASED_REELS"
IMAGE_ASPECT_RATIO: str = "16:9"
USES_AVATAR_REFERENCE: bool = False

# ---------------------------------------------------------------------------
# Cost / model tier (unused for ingest; kept so page_loader stays happy)
# ---------------------------------------------------------------------------
COST_TIER: str = "economic"
ENABLE_COST_TRACKING: bool = True
ECONOMIC_BRAIN_MODE: bool = True

# ---------------------------------------------------------------------------
# Source library — read-only from the factory; do not copy into the repo
# ---------------------------------------------------------------------------
SOURCE_DIRECTORY: str = (
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK\@ Contents"
    r"\You Tube Content Videos\Ray Dalio"
    r"\Ray Dalio Creation\Ray Dalio Principles of Wealth-Production"
)
REFERENCE_VIDEO_DIR: str = SOURCE_DIRECTORY
PROCESSED_SUBFOLDER: str = "Processed"

# ---------------------------------------------------------------------------
# Automation flags
# ---------------------------------------------------------------------------
DISCLAIMER_REQUIRED: bool = True
FINGERPRINT_BYPASS: bool = True
SHORTS_TO_LONG_LINKING: bool = True

# ---------------------------------------------------------------------------
# YouTube — SEO + RPM (page-exclusive; do not reuse on other pages)
# ---------------------------------------------------------------------------
YOUTUBE_CATEGORY_ID: str = "27"  # Education
YOUTUBE_DEFAULT_PRIVACY_LONG: str = "unlisted"
YOUTUBE_DEFAULT_PRIVACY_SHORT: str = "unlisted"
YOUTUBE_PLAYLIST_TITLE: str = "ACT I - The Foundations of Wealth Strategy"
YOUTUBE_PLAYLIST_DESCRIPTION: str = (
    "ACT I of Principles of Wealth — the foundations of wealth strategy. "
    "Independent educational analysis of economic frameworks and principles "
    "of wealth creation. Not affiliated with Ray Dalio or Bridgewater Associates."
)
YOUTUBE_DEFAULT_TAGS: list = [
    "financial education",
    "wealth building",
    "macroeconomics",
    "investing",
    "ray dalio analysis",
    "principles of wealth",
    "personal finance",
    "economics",
    "investing psychology",
    "wealth strategy",
]
SEO_TARGETS: list = [
    "Financial Education",
    "Wealth Building",
    "Macroeconomics",
    "Investing",
    "Ray Dalio Analysis",
]

YOUTUBE_TITLE_SUFFIX: str = " | Ray Dalio's Framework Analyzed (Financial Education)"
YOUTUBE_SHORT_TITLE_SUFFIX: str = " - Ray Dalio Analysis #shorts"

YOUTUBE_DESCRIPTION_TEMPLATE: str = (
    "In this educational analysis, we explore the economic frameworks and "
    "principles of wealth creation based on the philosophy of Ray Dalio.\n\n"
    "📈 Topics Covered: Macroeconomics, Investing Psychology, Wealth Building, "
    "and Financial Education.\n\n"
    "⚠️ Disclaimer: This channel provides independent educational curation and "
    "financial analysis. We are not affiliated with, endorsed by, or sponsored "
    "by Ray Dalio or Bridgewater Associates. All content is structured for "
    "educational purposes under fair use.\n\n"
    "#FinancialEducation #RayDalio #WealthBuilding #Investing #Economics"
)

YOUTUBE_SHORT_DESCRIPTION_TEMPLATE: str = (
    "Educational short based on Ray Dalio's principles of wealth.\n\n"
    "⚠️ Disclaimer: Independent educational curation and financial analysis. "
    "Not affiliated with, endorsed by, or sponsored by Ray Dalio or "
    "Bridgewater Associates.\n\n"
    "#FinancialEducation #RayDalio #WealthBuilding #Investing #Economics #shorts"
)

# ---------------------------------------------------------------------------
# Visual / caption defaults (ingest path; no generation)
# ---------------------------------------------------------------------------
FONT_PATH: str = "Fonts/Montserrat/static/Montserrat-Bold.ttf"
FONT_SIZE_SCALE: float = 0.07
FONT_COLOR: tuple = (245, 245, 240)
TEXT_OUTLINE_WIDTH: int = 2
LOGO_SIZE_SCALE: float = 0.28
LOGO_POSITION: str = "bottom_center"
CAPTION_SIGNATURE: str = "© Principles of Wealth | Educational Analysis"

ENABLE_SEQUENCE_REEL: bool = False
ENABLE_SKETCH_STYLE: bool = False
ENABLE_HORROR_TRANSFORMATIONS: bool = False
USE_STYLE_REFERENCE: bool = False
PINTEREST_BOARD_ID: str = ""

# ---------------------------------------------------------------------------
# Topic pool — 30-episode learning journey (catalog source of truth)
# ---------------------------------------------------------------------------
TOPIC_POOL: list = [
    "Ray Dalio's 5 Principles to Build Wealth",
    "Pain, Feedback, and Financial Growth",
    "The Radical Truth About Money Few Are Willing to Face",
    "How Economic Reality Really Works (Beyond Income and Effort)",
    "Ego: The Silent Tax on Investment Returns",
    "The Difference Between Risk and Ruin — Why Most Investors Never Recover",
    "Why Stability Beats High Returns — The Wealth Principle Most Investors Ignore",
    "Compounding Is a Law, Not a Strategy — Why Wealth Follows Structure",
    "Decision-Making Under Uncertainty — The Skill That Protects Wealth",
    "Designing Principles for Money — How Professionals Think About Wealth",
    "Your Personal Financial Machine — How Wealth Is Actually Engineered",
    "Cash Flow — The Lifeblood of Wealth | Why Net Worth Alone Fails",
    "Leverage Explained — Why Speed Destroys Wealth",
    "Debt Explained — When It Builds Wealth or Destroys It",
    "Diversification Is Not What You Think (This Is What Actually Protects Wealth)",
    "Patience Beats Talent: The Waiting Game Behind Real Wealth",
    "Automation, Discipline, and Emotional Distance",
    "Why Simple Systems Outperform Complex Ones Over Time",
    "Scaling Decisions as Capital Grows",
    "When to Change the Machine — and When Not To",
    "Cycles: Why Everything That Goes Up Eventually Tests You",
    "Surviving Drawdowns Without Breaking Your System",
    "The Psychology of Large Numbers",
    "Why Rich People Fear Different Things",
    "Transferring Principles Across Life and Business",
    "Mistakes That Only Appear at Higher Net Worths",
    "The Role of Humility in Long-Term Wealth",
    "Teaching the Machine to the Next Generation",
    "Freedom, Time, and What Wealth Is Actually For",
    "Living Inside Reality — A Lifetime Practice",
]

# ---------------------------------------------------------------------------
# Niche disclaimer — injected into LLM prompts AND YouTube descriptions
# ---------------------------------------------------------------------------
NICHE_DISCLAIMER: str = (
    "CHANNEL CONTEXT: Principles of Wealth — independent educational curation "
    "and financial analysis. Frame every title, caption, and description as "
    "Educational Curation / Analysis of economic frameworks. "
    "Never impersonate Ray Dalio or Bridgewater Associates. "
    "Never give personalised investment advice or stock tips. "
    "Always include the affiliation disclaimer. "
    "SEO targets: Financial Education, Wealth Building, Macroeconomics, "
    "Investing, Ray Dalio Analysis. "
    "Tone: clear, structured, professional, high-RPM finance education."
)
