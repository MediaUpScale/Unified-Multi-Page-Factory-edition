# -*- coding: utf-8 -*-
"""Production SEO pack for Principles of Wealth (longs, Shorts, playlists).

Titles follow ``[Person/Concept]: [High-Impact Principle] | Principles of Wealth``.
Descriptions are dense, keyword-rich, and end with a single plain-text disclaimer.
"""
from __future__ import annotations

from typing import Any, Optional

CHANNEL_BRAND = "Principles of Wealth"
TITLE_SUFFIX = " | Principles of Wealth"
_YT_TITLE_MAX = 100

DISCLAIMER = (
    "Disclaimer: Strictly educational content on financial analysis and "
    "macroeconomics. Not financial advice."
)

LONG_HASHTAGS = (
    "#PrinciplesOfWealth #WealthBuilding #Macroeconomics "
    "#PortfolioStrategy #CapitalPreservation"
)
SHORT_HASHTAGS = (
    "#Shorts #PrinciplesOfWealth #Finance #Investing #StockMarket #WealthBuilding"
)
PLAYLIST_HASHTAGS = (
    "#PrinciplesOfWealth #FinancialWisdom #Macroeconomics #PortfolioStrategy"
)

# YouTube tag hard cap is 30 chars. Keep every tag at or under that.
BASE_TAGS: tuple[str, ...] = (
    "principles of wealth",
    "stock market analysis",
    "S&P 500 strategy",
    "top growth stocks",
    "asset allocation",
    "inflation protection",
    "interest rate impact",
    "market crash prep",
    "macroeconomic framework",
    "wealth building systems",
    "institutional capital",
    "risk management",
    "wealth preservation",
    "ray dalio",
    "portfolio strategy",
)

# Macro angle injected into Shorts that have no hand-written row.
EPISODE_TRENDS: dict[int, str] = {
    1: "SPY concentration, Mag7 leadership, Treasury yields, and Fed funds path",
    2: "QQQ drawdowns, NVDA volatility, credit-spread spikes, and loss-recovery math",
    3: "earnings quality at AAPL MSFT GOOGL, CPI prints, and narrative vs cash flow",
    4: "SVB, Credit Suisse, FTX structure, TLT duration risk, and NVDA single-name risk",
    5: "TSLA sentiment cycles, AAPL buybacks, BTC crashes, and hedge-fund risk books",
    6: "levered ETFs, Archegos-style ruin, and why SPY crashes do not equal insolvency",
    7: "high-yield traps, HYG spreads, and why boring cash-flow beats lottery tickets",
    8: "dividend compounding vs Mag7 FOMO and the cost of interrupting a 401k plan",
    9: "FOMC days, CPI surprises, VIX spikes, and decision rules under incomplete data",
    10: "Bridgewater-style principles vs ad-hoc stock picking in a rate-shift regime",
    11: "personal financial operating systems versus chasing NVDA and meme-tape noise",
    12: "free cash flow at MSFT AMZN versus paper net worth in illiquid private marks",
    13: "margin debt, 3x ETFs, housing leverage, and why speed destroys capital",
    14: "investment-grade vs junk, student debt, and when leverage is a tool not a drug",
    15: "equal-weight RSP vs cap-weight SPY, Mag7 crowding, and true risk reduction",
    16: "waiting through Fed pauses while retail chases weekly options on NVDA TSLA",
    17: "auto-investing, tax-lot discipline, and emotional distance from CNBC tape",
    18: "three-fund simplicity versus 40-ticker complexity after a rate shock",
    19: "position sizing as AUM grows: from $10k SPY to concentrated founder stock",
    20: "when to rebalance a winning Mag7 book versus when to leave the machine alone",
    21: "debt cycles, housing, gold, and why every SPY bull market eventually tests you",
    22: "drawdown protocols used after 2022 TLT and Nasdaq, without abandoning the plan",
    23: "sequence-of-returns risk, large-number psychology, and retirement withdrawal math",
    24: "what capital-gains, key-person, and liquidity risk look like above high net worth",
    25: "transferring asset-allocation rules from markets into hiring, pricing, and cash",
    26: "estate, concentration, and lifestyle inflation that only appear after a liquidity event",
    27: "humility after a winning Mag7 decade and why overconfidence is a silent tax",
    28: "family investment policy statements, trusts, and teaching the next operator",
    29: "time freedom versus account size, and what wealth is actually supposed to buy",
    30: "living inside economic reality across inflation, deflation, and policy regimes",
}


def _fit_title(text: str, limit: int = _YT_TITLE_MAX) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    if not clean.endswith(TITLE_SUFFIX):
        clean = clean[:limit].rstrip(" |—-")
        return clean
    core = clean[: -len(TITLE_SUFFIX)].rstrip(" |—-")
    budget = limit - len(TITLE_SUFFIX)
    cut = core[: max(12, budget)].rstrip(" |—-,;:")
    if " " in cut and len(cut) > 20:
        cut = cut.rsplit(" ", 1)[0].rstrip(" |—-,;:")
    return f"{cut}{TITLE_SUFFIX}"[:limit]


def _merge_tags(*groups: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            tag = " ".join(str(raw).split()).strip()[:30]
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(tag)
            if len(out) >= 30:
                return out
    return out


def render_long_description(
    *,
    hook: str,
    key_concept: str,
    takeaways: list[str],
    keywords: str,
    hashtags: str = LONG_HASHTAGS,
) -> str:
    t = [item.strip() for item in takeaways]
    while len(t) < 5:
        t.append("resilient asset structures across changing market cycles")
    return "\n".join(
        [
            hook.strip(),
            "",
            (
                f"Welcome to {CHANNEL_BRAND}. In this episode, we break down "
                f"{key_concept} into an actionable framework for risk management, "
                "capital preservation, and long-term asset growth. This is independent "
                "educational analysis of published wealth principles — built to help you "
                "think like an operator of capital, not a consumer of market noise."
            ),
            "",
            "Key takeaways:",
            f"Why {t[0]}",
            f"How {t[1]}",
            f"Bridging {t[2]}",
            f"Protecting wealth by {t[3]}",
            f"Building {t[4]}",
            "",
            keywords.strip(),
            "",
            (
                "Designed for deep, focused listening to help you absorb complex "
                "financial concepts efficiently. Subscribe to Principles of Wealth "
                "for actionable macro perspectives."
            ),
            "",
            hashtags.strip(),
            "",
            DISCLAIMER,
        ]
    )


def render_short_description(
    *,
    opening: str,
    angle: str,
    long_video_id: str = "",
    hashtags: str = SHORT_HASHTAGS,
) -> str:
    link = ""
    if long_video_id:
        link = (
            f"Watch the full deep-dive breakdown: https://youtu.be/{long_video_id}\n\n"
        )
    return "\n".join(
        [
            opening.strip(),
            "",
            angle.strip(),
            "",
            (
                "As macroeconomic conditions shift, individual investors often fall "
                "into the trap of short-term noise. Understanding how institutional "
                "capital navigates market shifts, inflation data, and interest rate "
                "trends is critical for capital preservation. The Principles of Wealth "
                "model prioritizes long-term risk asymmetry over speculative timing, "
                "ensuring your portfolio withstands market volatility in SPY, QQQ, "
                "Treasuries, and concentrated growth names."
            ),
            "",
            (
                f"{link}Watch the full deep-dive breakdown on the Principles of Wealth "
                "channel or access the linked playlist for the complete strategy."
            ),
            "",
            hashtags.strip(),
            "",
            DISCLAIMER,
        ]
    )


def render_playlist_description(*, theme: str, episode_lines: list[str]) -> str:
    listed = "\n".join(episode_lines)
    return "\n".join(
        [
            (
                "A structured series by Principles of Wealth analyzing macroeconomics, "
                "investor psychology, and resilient capital preservation strategies. "
                f"{theme} Decoupling market noise from institutional realities to build "
                "sustainable long-term wealth across S&P 500 cycles, rate regimes, and "
                "inflation shocks."
            ),
            "",
            "Included Episodes:",
            listed,
            "",
            PLAYLIST_HASHTAGS,
            "",
            DISCLAIMER,
        ]
    )


# title, key_concept, hook (3 sentences), takeaways (5), keywords paragraph, extra tags
_LONGS: dict[int, dict[str, Any]] = {
    1: {
        "title": "Ray Dalio: 5 Wealth Rules He Never Breaks | Principles of Wealth",
        "key_concept": "five non-negotiable wealth rules",
        "hook": (
            "Most investors treat wealth as a scoreboard of income, bonuses, and the latest "
            "SPY or NVDA print — then wonder why a rate shock still feels like a personal crisis.\n"
            "Macroeconomic reality is colder: wealth is a system that survives inflation, "
            "Fed policy shifts, and concentrated Mag7 drawdowns without forcing you to sell.\n"
            "This episode contrasts the retail habit of chasing top growth stocks with the "
            "institutional habit of writing rules you cannot break when the tape turns violent."
        ),
        "takeaways": [
            "written principles outweigh speculative trading in portfolio strategy when SPY leadership rotates",
            "capital-preservation rules adapt to structural inflation and interest-rate impact on stocks",
            "theoretical economic models with real-world asset allocation used by institutional capital",
            "avoiding ruin rather than chasing high-yield traps in HYG, crypto, and levered products",
            "resilient asset structures across changing market cycles instead of one-ticker bets",
        ],
        "keywords": (
            "Search this framework when you need principles of wealth, stock market trend analysis, "
            "S&P 500 strategy, asset allocation models, inflation protection strategies, interest rate "
            "impact on stocks, market crash preparation, a macroeconomic framework, wealth building "
            "systems, institutional capital strategies, risk management investing, and long term wealth "
            "preservation. We map Dalio-style decision rules onto Mag7 concentration, Treasury yields, "
            "and the difference between a job and a financial machine."
        ),
        "tags": ["5 wealth principles", "wealth rules", "fed policy"],
        "hashtags": (
            "#PrinciplesOfWealth #RayDalio #WealthBuilding #Macroeconomics #AssetAllocation"
        ),
    },
    2: {
        "title": "Ray Dalio: Pain, Feedback, and Financial Growth | Principles of Wealth",
        "key_concept": "pain as financial data and feedback as a compounding skill",
        "hook": (
            "Retail culture treats a red day in QQQ or a NVDA air-pocket as a character flaw — "
            "something to mute, revenge-trade, or hide from the 401k statement.\n"
            "Institutional books treat the same pain as information: a signal about sizing, "
            "liquidity, and whether your S&P 500 strategy was a plan or a hope.\n"
            "This episode shows why avoiding feedback is more expensive than the loss itself "
            "when inflation prints, credit spreads, and rate paths are rewriting the tape."
        ),
        "takeaways": [
            "processed losses outweigh denial in any serious risk-management investing process",
            "feedback loops adapt to structural inflation and interest-rate impact on stocks",
            "theoretical learning models with real-world drawdown journals used by professionals",
            "avoiding ruin rather than doubling down after a Mag7 or crypto shock",
            "resilient judgment across market cycles by treating pain as data, not identity",
        ],
        "keywords": (
            "Use this episode for wealth building systems, institutional capital strategies, "
            "market crash preparation, and long term wealth preservation. We connect pain-as-data "
            "to QQQ drawdowns, NVDA volatility, credit-spread spikes, and the recovery math that "
            "separates operators from spectators after a CPI or FOMC surprise."
        ),
        "tags": ["pain is data", "drawdown psychology", "QQQ"],
        "hashtags": (
            "#PrinciplesOfWealth #InvestorPsychology #RiskManagement #StockMarket #WealthBuilding"
        ),
    },
    3: {
        "title": "Ray Dalio: The Radical Truth About Money | Principles of Wealth",
        "key_concept": "radical truth versus self-deception in money",
        "hook": (
            "The common financial misconception is that optimism is an asset — that believing "
            "harder in AAPL, MSFT, or a private mark will convert a fragile balance sheet into wealth.\n"
            "Macroeconomic reality is that markets pay you for seeing cash flow, rates, and "
            "inflation as they are, not as your identity needs them to be.\n"
            "This episode is about the expensive gap between a story you tell yourself and "
            "the numbers a credit committee, a CIO, or a family office actually underwrites."
        ),
        "takeaways": [
            "radical clarity outweighs optimistic narratives in portfolio strategy and career bets",
            "truth-seeking adapts when CPI, payrolls, and Treasury yields rewrite the consensus",
            "theoretical economic models with forensic honesty about earnings quality and leverage",
            "avoiding ruin rather than defending a thesis that the tape has already killed",
            "resilient asset structures by replacing financial illusions with observable reality",
        ],
        "keywords": (
            "Keyword map: principles of wealth, macroeconomic framework, stock market trend analysis, "
            "S&P 500 strategy, inflation protection strategies, and risk management investing. "
            "We contrast earnings quality at mega-cap tech with narrative stocks, and show why "
            "self-deception is a hidden tax on capital preservation."
        ),
        "tags": ["radical truth", "earnings quality", "CPI"],
        "hashtags": (
            "#PrinciplesOfWealth #Macroeconomics #InvestingPsychology #CapitalPreservation #Finance"
        ),
    },
    4: {
        "title": "Ray Dalio: How Economic Reality Really Works | Principles of Wealth",
        "key_concept": "economic reality beyond income, effort, and job titles",
        "hook": (
            "Silicon Valley salaries, 80-hour weeks, and 'hustle' did not save depositors at SVB, "
            "did not save Credit Suisse's reputation, and did not save FTX customers from structure.\n"
            "Macroeconomic reality is systems: duration risk in TLT, single-name risk in NVDA, "
            "dollar plumbing, and why the S&P 500 can survive companies that individual balance sheets cannot.\n"
            "This episode separates effort from economic function so you stop confusing a paycheck "
            "with a wealth-building system."
        ),
        "takeaways": [
            "system design outweighs raw effort when banks, brokers, and exchanges fail",
            "asset allocation adapts to inflation, rate shocks, and 'safe' bonds that were not safe",
            "theoretical economic models with case studies: SVB, Credit Suisse, FTX, and 2022 duration",
            "avoiding ruin rather than assuming hard work immunizes you from liquidity events",
            "resilient structures that survive cycles even when a favorite ticker does not",
        ],
        "keywords": (
            "Built for searches on market crash preparation, interest rate impact on stocks, "
            "S&P 500 strategy, inflation protection, institutional capital strategies, and "
            "long term wealth preservation. Tickers and institutions in view: SPY, NVDA, TLT, "
            "the US dollar, SVB, Credit Suisse, and crypto market structure — not as tips, as mechanics."
        ),
        "tags": ["economic reality", "SVB", "Credit Suisse", "TLT"],
        "hashtags": (
            "#PrinciplesOfWealth #S&P500 #Inflation #InterestRates #MarketCrash"
        ),
    },
    5: {
        "title": "Ray Dalio: Ego, the Silent Tax on Returns | Principles of Wealth",
        "key_concept": "ego as a silent tax on investment returns",
        "hook": (
            "The misconception is that conviction is the same as edge — that being loud on TSLA, "
            "BTC, or a concentrated Mag7 book proves you are a serious investor.\n"
            "Macroeconomic reality is that ego turns volatility into ruin: you refuse to cut, "
            "you average down for pride, and you confuse a personality with a position.\n"
            "This episode studies how hedge funds, Apple-style capital discipline, and emotional "
            "crypto collapses teach the same lesson: identity is not an asset class."
        ),
        "takeaways": [
            "humility outweighs theatrical conviction in risk management investing",
            "position sizing adapts when sentiment, rates, and liquidity shift under your thesis",
            "theoretical psychology with real-world TSLA, AAPL, and BTC behavioral case studies",
            "avoiding ruin rather than defending an ego position into insolvency",
            "resilient process by separating who you are from what you own",
        ],
        "keywords": (
            "SEO cluster: wealth building systems, investor psychology, institutional capital "
            "strategies, top growth stocks, and market crash preparation. We examine why Tesla "
            "punishes emotional capital, how Apple models discipline, and why crypto wipeouts "
            "are often ego events dressed up as 'long-term vision.'"
        ),
        "tags": ["ego tax", "TSLA", "investor psychology"],
        "hashtags": (
            "#PrinciplesOfWealth #InvestorPsychology #RiskManagement #Bitcoin #Tesla"
        ),
    },
    6: {
        "title": "Ray Dalio: Risk vs Ruin — Why Most Investors Never Recover | Principles of Wealth",
        "key_concept": "the difference between risk and ruin",
        "hook": (
            "Retail language treats every SPY dip as 'risk' and every recovery as proof you were right — "
            "which is how people confuse a 20% drawdown with a 70% hole you cannot climb.\n"
            "Macroeconomic reality is asymmetric: risk is a distribution you can survive; ruin is "
            "leverage, concentration, and liquidity that deletes the game.\n"
            "This episode is the math and the psychology of never taking a bet that can take you out."
        ),
        "takeaways": [
            "asymmetric survival outweighs maximizing average return in portfolio strategy",
            "ruin constraints adapt to rate shocks, levered ETFs, and single-name blowups",
            "theoretical risk models with real-world recovery math after deep drawdowns",
            "avoiding ruin rather than treating max-pain as a personality test",
            "resilient books that can still compound after the worst plausible tape",
        ],
        "keywords": (
            "For market crash preparation, risk management investing, long term wealth preservation, "
            "and S&P 500 strategy. We distinguish volatility from insolvency, explain why 3x products "
            "and margin turn ordinary risk into ruin, and how institutional books pre-commit to survival."
        ),
        "tags": ["risk vs ruin", "leverage", "drawdown math"],
        "hashtags": (
            "#PrinciplesOfWealth #RiskManagement #MarketCrash #CapitalPreservation #Investing"
        ),
    },
    7: {
        "title": "Ray Dalio: Why Stability Beats High Returns | Principles of Wealth",
        "key_concept": "stability as a higher-order return",
        "hook": (
            "High-yield pitches, weekly options, and 'this HYG coupon replaces my salary' are the "
            "most popular financial misconceptions of every late-cycle tape.\n"
            "Macroeconomic reality is that a stable compounding engine beats a spectacular year "
            "that forces you to reset the clock after a credit or rate event.\n"
            "This episode explains why professionals pay for dull cash-flow and hate lottery tickets "
            "that look like top growth stocks until they do not."
        ),
        "takeaways": [
            "stability of process outweighs headline yield in any serious asset allocation model",
            "cash-flow quality adapts when inflation and interest rates reprice credit",
            "theoretical return math with real-world high-yield traps and credit-spread history",
            "avoiding ruin rather than stretching for yield in junk, crypto, and levered notes",
            "resilient income structures that survive cycle turns instead of marketing-cycle yields",
        ],
        "keywords": (
            "Keyword set: inflation protection strategies, interest rate impact on stocks, "
            "wealth building systems, institutional capital strategies, and long term wealth "
            "preservation. Contrast boring compounding with high-yield theater around HYG, "
            "speculative credit, and social-media 'passive income' claims."
        ),
        "tags": ["stability", "high yield trap", "HYG"],
        "hashtags": (
            "#PrinciplesOfWealth #WealthBuilding #CreditMarkets #Inflation #PortfolioStrategy"
        ),
    },
    8: {
        "title": "Ray Dalio: Compounding Is a Law, Not a Strategy | Principles of Wealth",
        "key_concept": "compounding as a law of structure, not a tactic",
        "hook": (
            "People 'do compounding' the way they 'do a diet' — start, interrupt for Mag7 FOMO, "
            "stop contributions, then blame the S&P 500 strategy for failing them.\n"
            "Macroeconomic reality is that compounding is what happens when you do not break the "
            "machine: contributions, low leak, and time across rate cycles.\n"
            "This episode treats compounding as physics for money, not a motivational slogan."
        ),
        "takeaways": [
            "uninterrupted structure outweighs clever entry timing in long-term S&P 500 strategy",
            "contribution discipline adapts through inflation, recessions, and Fed pauses",
            "theoretical compound-interest models with real 401k leak, tax, and behavior costs",
            "avoiding ruin of the compounding clock rather than chasing a hotter ticker",
            "resilient automatic systems that keep compounding when headlines are unwatchable",
        ],
        "keywords": (
            "Use for wealth building systems, long term wealth preservation, asset allocation models, "
            "and stock market trend analysis. We contrast dividend and index compounding with "
            "interruptions caused by options lottery tickets on NVDA and TSLA."
        ),
        "tags": ["compounding", "401k", "time in market"],
        "hashtags": (
            "#PrinciplesOfWealth #Compounding #WealthBuilding #IndexInvesting #S&P500"
        ),
    },
    9: {
        "title": "Ray Dalio: Decision-Making Under Uncertainty | Principles of Wealth",
        "key_concept": "decision rules under incomplete information",
        "hook": (
            "FOMC days, CPI prints, and VIX spikes create the illusion that the next headline "
            "will finally give you certainty — so you wait, then overtrade the release.\n"
            "Macroeconomic reality is permanent uncertainty: rates, growth, and geopolitics "
            "never resolve into a clean spreadsheet.\n"
            "This episode is about building a decision process that still allocates capital "
            "when the data is noisy, late, and contradictory."
        ),
        "takeaways": [
            "pre-committed rules outweigh prediction in institutional capital strategies",
            "scenario trees adapt to inflation surprises and interest-rate impact on stocks",
            "theoretical decision science with real FOMC, CPI, and payroll-week behavior",
            "avoiding ruin from all-in bets made to 'feel certain' after a data print",
            "resilient judgment that separates signal from CNBC-cycle noise",
        ],
        "keywords": (
            "SEO: macroeconomic framework, market crash preparation, risk management investing, "
            "S&P 500 strategy, and principles of wealth. Decision-making under uncertainty is "
            "the skill that protects wealth when VIX, yields, and Mag7 leadership disagree."
        ),
        "tags": ["decision making", "FOMC", "VIX"],
        "hashtags": (
            "#PrinciplesOfWealth #Macroeconomics #FOMC #RiskManagement #Investing"
        ),
    },
    10: {
        "title": "Ray Dalio: Designing Principles for Money | Principles of Wealth",
        "key_concept": "designing personal principles for money the way professionals do",
        "hook": (
            "Most people collect tips — a stock, a guru, a thread — and call that a philosophy "
            "until the first rate cycle proves they had a mood, not a design.\n"
            "Macroeconomic reality rewards written principles that still work when SPY, credit, "
            "and inflation disagree with last year's playbook.\n"
            "This episode is how to design money rules you can audit, stress-test, and keep "
            "when the tape is ugly."
        ),
        "takeaways": [
            "designed principles outweigh ad-hoc stock picking in portfolio strategy",
            "personal investment policy adapts to structural inflation and regime shifts",
            "theoretical principle-design with real-world IPS, rebalance, and risk-budget practice",
            "avoiding ruin by forbidding bets your principles cannot survive",
            "resilient wealth operating systems instead of personality-driven trading",
        ],
        "keywords": (
            "For asset allocation models, institutional capital strategies, wealth building systems, "
            "and long term wealth preservation. We translate professional principle-design into "
            "household policy: what you own, what you never own, and how you behave on crash days."
        ),
        "tags": ["investment policy", "money principles", "IPS"],
        "hashtags": (
            "#PrinciplesOfWealth #PortfolioStrategy #WealthBuilding #AssetAllocation #Finance"
        ),
    },
    11: {
        "title": "Ray Dalio: Your Personal Financial Machine | Principles of Wealth",
        "key_concept": "the personal financial machine",
        "hook": (
            "People upgrade income and still run a hobbyist money life — screens, tips, and "
            "NVDA chats — then call the chaos a 'portfolio.'\n"
            "Macroeconomic reality is that wealth is engineered: inputs, conversion, storage, "
            "and leak control, the same way a business engineers cash conversion.\n"
            "This episode is the schematic of a household financial machine that still runs "
            "when Mag7 leadership or a Fed pivot dominates the news."
        ),
        "takeaways": [
            "machine design outweighs talent stories in long-term wealth building systems",
            "cash conversion adapts when rates, inflation, and job markets shift",
            "theoretical systems thinking with real budgeting, investing, and insurance plumbing",
            "avoiding ruin from a lifestyle that only works in a bull tape",
            "resilient operating cadence: measure, allocate, review, without drama",
        ],
        "keywords": (
            "Keyword cluster: principles of wealth, wealth building systems, asset allocation models, "
            "institutional capital strategies, and S&P 500 strategy. Your personal financial machine "
            "is how professionals think about money when the alternative is chasing tape."
        ),
        "tags": ["financial machine", "cash conversion", "systems"],
        "hashtags": (
            "#PrinciplesOfWealth #WealthBuilding #PersonalFinance #SystemsThinking #Investing"
        ),
    },
    12: {
        "title": "Ray Dalio: Cash Flow vs Net Worth — What Fails | Principles of Wealth",
        "key_concept": "cash flow as the lifeblood versus paper net worth",
        "hook": (
            "Net-worth screenshots — private marks, home equity, a concentrated MSFT or AMZN "
            "position — create the misconception that you are liquid and safe.\n"
            "Macroeconomic reality is cash flow: what arrives, what is obligated, and what "
            "survives if marks gap down or credit tightens.\n"
            "This episode is why paper wealth fails under inflation, rate shocks, and illiquidity "
            "even when the headline number still looks impressive."
        ),
        "takeaways": [
            "free-cash-flow thinking outweighs vanity net worth in capital preservation",
            "liquidity planning adapts when interest rates reprice assets and liabilities",
            "theoretical accounting with real household and mega-cap cash-flow case studies",
            "avoiding ruin from illiquid 'rich' that cannot pay a margin or a tax bill",
            "resilient income architecture underneath whatever the market is marking today",
        ],
        "keywords": (
            "For inflation protection strategies, risk management investing, long term wealth "
            "preservation, and stock market trend analysis. We contrast free cash flow at quality "
            "compounders with paper net worth that vanishes in a liquidity event."
        ),
        "tags": ["cash flow", "net worth", "liquidity"],
        "hashtags": (
            "#PrinciplesOfWealth #CashFlow #Liquidity #WealthBuilding #Macroeconomics"
        ),
    },
    13: {
        "title": "Ray Dalio: Leverage Explained — Why Speed Destroys Wealth | Principles of Wealth",
        "key_concept": "leverage as speed that can destroy wealth",
        "hook": (
            "Leverage is sold as intelligence — margin, 3x ETFs, extra property, options overlay — "
            "because it makes a good year look like genius.\n"
            "Macroeconomic reality is that speed cuts both ways: the same structure that "
            "amplifies SPY on the way up amplifies ruin when yields gap or liquidity disappears.\n"
            "This episode explains leverage as a time-compressor, not a personality trait."
        ),
        "takeaways": [
            "unlevered survival outweighs levered brilliance in risk management investing",
            "leverage budgets adapt when volatility, rates, and haircuts change",
            "theoretical leverage math with real margin, housing, and levered-ETF case studies",
            "avoiding ruin rather than treating speed as a substitute for edge",
            "resilient growth that does not require a perfect tape to remain solvent",
        ],
        "keywords": (
            "SEO: market crash preparation, interest rate impact on stocks, institutional capital "
            "strategies, and S&P 500 strategy. Leverage explained for people who think 'just a "
            "little margin' is harmless until a gap through the stop."
        ),
        "tags": ["leverage", "margin debt", "3x ETF"],
        "hashtags": (
            "#PrinciplesOfWealth #Leverage #RiskManagement #ETFs #CapitalPreservation"
        ),
    },
    14: {
        "title": "Ray Dalio: Debt Explained — When It Builds or Destroys | Principles of Wealth",
        "key_concept": "debt as a tool that either builds or destroys wealth",
        "hook": (
            "Culture splits into two superstitions: all debt is evil, or cheap debt is always "
            "genius — both collapse when the Fed, credit spreads, and cash flow disagree.\n"
            "Macroeconomic reality is conditional: productive debt against durable cash flow "
            "is a tool; lifestyle and speculative debt is a fuse.\n"
            "This episode is a decision framework for when borrowing is architecture and when "
            "it is a countdown."
        ),
        "takeaways": [
            "cash-flow coverage outweighs rate-shopping as the primary debt test",
            "debt structure adapts when inflation and policy rates reprice the liability",
            "theoretical credit analysis with real mortgage, student, and corporate debt cases",
            "avoiding ruin from floating-rate and covenant surprises",
            "resilient liability design that still works if growth disappoints",
        ],
        "keywords": (
            "Keyword map: inflation protection, interest rate impact, wealth building systems, "
            "and long term wealth preservation. Debt explained without slogans — investment-grade "
            "versus junk logic applied to households, not just HYG."
        ),
        "tags": ["debt", "credit", "mortgage"],
        "hashtags": (
            "#PrinciplesOfWealth #Debt #InterestRates #CreditMarkets #WealthBuilding"
        ),
    },
    15: {
        "title": "Ray Dalio: Diversification Is Not What You Think | Principles of Wealth",
        "key_concept": "true diversification versus ticker count",
        "hook": (
            "Owning 12 Mag7 lookalikes, three Nasdaq funds, and a 'diversified' growth sleeve "
            "is the most common diversification misconception of the cap-weight era.\n"
            "Macroeconomic reality is correlation: when rates jump, duration, growth, and "
            "private marks can all fail together while you thought you were spread out.\n"
            "This episode is what actually reduces ruin risk — not how many logos are in the app."
        ),
        "takeaways": [
            "uncorrelated drivers outweigh ticker count in asset allocation models",
            "diversification adapts when Mag7 crowding and cap-weight SPY dominate returns",
            "theoretical portfolio theory with real RSP vs SPY and 60/40 breakdowns",
            "avoiding ruin from concentrated 'diversification' that is one factor in costume",
            "resilient mix across inflation, growth, and liquidity regimes",
        ],
        "keywords": (
            "For S&P 500 strategy, top growth stocks, inflation protection strategies, "
            "macroeconomic framework, and risk management investing. Diversification is not "
            "what you think if your book is just NVDA with extra steps."
        ),
        "tags": ["diversification", "Mag7", "correlation"],
        "hashtags": (
            "#PrinciplesOfWealth #Diversification #S&P500 #AssetAllocation #PortfolioStrategy"
        ),
    },
    16: {
        "title": "Ray Dalio: Patience Beats Talent Behind Real Wealth | Principles of Wealth",
        "key_concept": "patience as the scarce skill behind real wealth",
        "hook": (
            "Talent is over-credited: the person who can pick NVDA or time a CPI print looks "
            "brilliant until you measure who was still invested, still contributing, still calm.\n"
            "Macroeconomic reality is that waiting through Fed pauses and boring years is the "
            "edge most 'talented' traders liquidate.\n"
            "This episode is the waiting game as a professional practice, not a personality quirk."
        ),
        "takeaways": [
            "time-in-structure outweighs cleverness in long term wealth preservation",
            "patience adapts when weekly options and social feeds compress your horizon",
            "theoretical compounding with real behavior gaps versus buy-and-hold indexes",
            "avoiding ruin from boredom trades that feel like talent",
            "resilient patience protocols for rate cycles and sideways tapes",
        ],
        "keywords": (
            "SEO: wealth building systems, stock market trend analysis, S&P 500 strategy, "
            "and institutional capital strategies. Patience beats talent when the alternative "
            "is gambling the compounding clock on TSLA weeklies."
        ),
        "tags": ["patience", "time in market", "behavior gap"],
        "hashtags": (
            "#PrinciplesOfWealth #InvestorPsychology #Compounding #LongTermInvesting #WealthBuilding"
        ),
    },
    17: {
        "title": "Ray Dalio: Automation, Discipline, Emotional Distance | Principles of Wealth",
        "key_concept": "automation, discipline, and emotional distance",
        "hook": (
            "Willpower is a terrible wealth strategy — it fails on FOMC week, on a red NVDA day, "
            "and whenever the feed is louder than your plan.\n"
            "Macroeconomic reality is that professionals automate the boring parts and keep "
            "emotional distance from CNBC-cycle noise so the machine still posts contributions.\n"
            "This episode is how to design discipline as infrastructure, not as a mood."
        ),
        "takeaways": [
            "automated contributions outweigh heroic self-control in wealth building systems",
            "review cadence adapts to inflation prints without turning every print into a trade",
            "theoretical behavior design with real tax-lot, DCA, and rebalance automation",
            "avoiding ruin from panic de-risking at the worst liquidity moment",
            "resilient emotional distance that still allows human judgment at policy level",
        ],
        "keywords": (
            "Keyword set: risk management investing, institutional capital strategies, "
            "market crash preparation, and long term wealth preservation. Automation is how "
            "you survive interest-rate impact on stocks without living inside the tape."
        ),
        "tags": ["automation", "DCA", "discipline"],
        "hashtags": (
            "#PrinciplesOfWealth #Discipline #InvestorPsychology #DollarCostAveraging #Investing"
        ),
    },
    18: {
        "title": "Ray Dalio: Why Simple Systems Outperform Complex Ones | Principles of Wealth",
        "key_concept": "simplicity as an edge over complex portfolios",
        "hook": (
            "Complexity feels like sophistication — 40 tickers, overlays, factors, and a "
            "spreadsheet that only you understand — until a rate shock reveals you cannot execute.\n"
            "Macroeconomic reality is that simple systems get followed; complex ones get "
            "abandoned at the exact moment process would have paid.\n"
            "This episode is why three-fund clarity often beats a Rube Goldberg book."
        ),
        "takeaways": [
            "executable simplicity outweighs unexecutable intelligence in portfolio strategy",
            "simple mixes adapt across inflation and growth regimes without constant tinkering",
            "theoretical robustness with real household three-fund versus 40-ticker failure modes",
            "avoiding ruin from complexity you cannot monitor in a crisis",
            "resilient systems a tired human can still run on a crash Monday",
        ],
        "keywords": (
            "For asset allocation models, S&P 500 strategy, wealth building systems, and "
            "macroeconomic framework. Simple systems outperform complex ones over time because "
            "behavior, not brilliance, is the bottleneck."
        ),
        "tags": ["simple systems", "index funds", "robustness"],
        "hashtags": (
            "#PrinciplesOfWealth #AssetAllocation #IndexInvesting #PortfolioStrategy #WealthBuilding"
        ),
    },
    19: {
        "title": "Ray Dalio: Scaling Decisions as Capital Grows | Principles of Wealth",
        "key_concept": "scaling decisions as AUM and net worth grow",
        "hook": (
            "The playbook that worked at $10k in SPY becomes a different animal at concentrated "
            "employer stock, a business, or a sudden liquidity event — but people keep the old habits.\n"
            "Macroeconomic reality is that size changes liquidity, tax, and ruin math; it does "
            "not grant immunity from inflation or rate cycles.\n"
            "This episode is how decision quality must scale with capital without scaling ego."
        ),
        "takeaways": [
            "position-size rules outweigh leftover lottery habits as capital grows",
            "governance adapts when single-name and private holdings dominate the book",
            "theoretical scaling with real concentrated-stock, RSU, and founder-wealth cases",
            "avoiding ruin from 'it worked when I was small' applied to a larger surface area",
            "resilient decision rights, reviews, and risk budgets at each wealth stage",
        ],
        "keywords": (
            "SEO: institutional capital strategies, risk management investing, asset allocation "
            "models, and long term wealth preservation. Scaling decisions as capital grows is "
            "how you stop treating a fortune like a hobby account."
        ),
        "tags": ["scaling capital", "RSUs", "position sizing"],
        "hashtags": (
            "#PrinciplesOfWealth #PositionSizing #WealthBuilding #RiskManagement #Finance"
        ),
    },
    20: {
        "title": "Ray Dalio: When to Change the Machine — and When Not To | Principles of Wealth",
        "key_concept": "when to change the financial machine and when to leave it alone",
        "hook": (
            "Every Mag7 melt-up or Fed pivot creates a stampede to 'upgrade the strategy' — "
            "which is often just boredom and FOMO wearing a research costume.\n"
            "Macroeconomic reality is that some regime shifts do require a redesign, and most "
            "headlines do not.\n"
            "This episode is a filter: change the machine for structure, not for stimulation."
        ),
        "takeaways": [
            "regime filters outweigh headline-driven overhauls in portfolio strategy",
            "rebalance rules adapt to inflation and rates without abandoning the core engine",
            "theoretical change-management with real 60/40, Mag7, and policy-shift case studies",
            "avoiding ruin from constant strategy-hopping that crystallizes losses",
            "resilient review calendars that allow change without addiction to change",
        ],
        "keywords": (
            "Keyword cluster: stock market trend analysis, macroeconomic framework, S&P 500 "
            "strategy, and wealth building systems. Knowing when not to change the machine is "
            "as valuable as knowing when a real regime has arrived."
        ),
        "tags": ["rebalancing", "regime shift", "policy"],
        "hashtags": (
            "#PrinciplesOfWealth #PortfolioStrategy #Rebalancing #Macroeconomics #Investing"
        ),
    },
    21: {
        "title": "Ray Dalio: Cycles — Why What Goes Up Eventually Tests You | Principles of Wealth",
        "key_concept": "debt and market cycles that eventually test every winner",
        "hook": (
            "Late-cycle confidence treats SPY, housing, and gold as one-way streets — until "
            "the debt cycle, policy, or inflation reminds you that 'up' is not a personality.\n"
            "Macroeconomic reality is cyclical: credit, asset prices, and psychology overshoot "
            "together and then test whoever had no plan for the other side.\n"
            "This episode is cycle literacy as a capital-preservation skill, not a forecast hobby."
        ),
        "takeaways": [
            "cycle literacy outweighs linear extrapolation in long-term S&P 500 strategy",
            "positioning adapts as debt, inflation, and policy regimes rotate",
            "theoretical cycle models with real housing, equity, and gold history as maps not oracles",
            "avoiding ruin from 'this time the cycle is cancelled' narratives",
            "resilient plans that assume tests even while participating in expansions",
        ],
        "keywords": (
            "For macroeconomic framework, inflation protection strategies, market crash "
            "preparation, and interest rate impact on stocks. Cycles: why everything that goes "
            "up eventually tests you — including the parts of the book you were sure were safe."
        ),
        "tags": ["debt cycle", "housing", "gold"],
        "hashtags": (
            "#PrinciplesOfWealth #DebtCycle #Macroeconomics #Inflation #MarketCycles"
        ),
    },
    22: {
        "title": "Ray Dalio: Surviving Drawdowns Without Breaking Your System | Principles of Wealth",
        "key_concept": "surviving drawdowns without breaking the system",
        "hook": (
            "A drawdown in Nasdaq or TLT feels like the system failed — so people sell the "
            "engine to stop the feeling, then buy it back higher.\n"
            "Macroeconomic reality is that drawdowns are how markets transfer assets from "
            "the impatient to the pre-committed.\n"
            "This episode is a protocol for staying inside the machine when 2022-style "
            "duration and growth pain show up again."
        ),
        "takeaways": [
            "pre-committed drawdown rules outweigh improvisation in market crash preparation",
            "liquidity buffers adapt so you are not a forced seller into a gap",
            "theoretical recovery math with real 2022 TLT and Nasdaq behavior",
            "avoiding ruin from abandoning a sound process at maximum pessimism",
            "resilient psychological and cash structures that keep contributions alive",
        ],
        "keywords": (
            "SEO: risk management investing, long term wealth preservation, S&P 500 strategy, "
            "and institutional capital strategies. Surviving drawdowns without breaking your "
            "system is the difference between a plan and a mood."
        ),
        "tags": ["drawdowns", "TLT", "Nasdaq"],
        "hashtags": (
            "#PrinciplesOfWealth #Drawdowns #MarketCrash #RiskManagement #Investing"
        ),
    },
    23: {
        "title": "Ray Dalio: The Psychology of Large Numbers | Principles of Wealth",
        "key_concept": "the psychology of large numbers and sequence risk",
        "hook": (
            "Percentages lie once the account is large: a 'normal' 25% year on a big number "
            "feels like a catastrophe, and people start protecting ego instead of process.\n"
            "Macroeconomic reality is sequence-of-returns, withdrawal math, and the fact that "
            "large numbers change behavior faster than they change the underlying engine.\n"
            "This episode is how to stay numerically sane when the dollar swings get loud."
        ),
        "takeaways": [
            "process percentages outweigh dollar-pain theater in risk management investing",
            "withdrawal and contribution rules adapt as balances enter a psychologically new zone",
            "theoretical sequence risk with real retirement and concentrated-wealth case studies",
            "avoiding ruin from panic changes triggered by large nominal swings",
            "resilient reporting that shows units and policy, not just a scary dollar line",
        ],
        "keywords": (
            "Keyword map: wealth building systems, long term wealth preservation, asset allocation "
            "models, and macroeconomic framework. The psychology of large numbers is why some "
            "people get richer on paper and poorer in behavior."
        ),
        "tags": ["sequence risk", "retirement", "behavior"],
        "hashtags": (
            "#PrinciplesOfWealth #Retirement #InvestorPsychology #WealthBuilding #Finance"
        ),
    },
    24: {
        "title": "Ray Dalio: Why Rich People Fear Different Things | Principles of Wealth",
        "key_concept": "how risk perception changes with wealth",
        "hook": (
            "From the outside, high net worth looks like the end of fear. From the inside, "
            "the fears just change: liquidity, key person, lawsuits, taxes, and concentration.\n"
            "Macroeconomic reality is that capital always has a predator — inflation, policy, "
            "or a single-stock gap — and richer people simply see a different predator set.\n"
            "This episode translates those fears into design problems, not vibes."
        ),
        "takeaways": [
            "mapped fears outweigh vague anxiety in institutional-style capital preservation",
            "insurance and liquidity design adapt as the surface area of wealth expands",
            "theoretical risk taxonomy with real key-person, lawsuit, and tax-timing cases",
            "avoiding ruin from ignoring the new risks that appear after a liquidity event",
            "resilient structures that match the actual fear set of the current balance sheet",
        ],
        "keywords": (
            "For institutional capital strategies, risk management investing, long term wealth "
            "preservation, and inflation protection. Why rich people fear different things — "
            "and how those fears should change the machine, not just the mood."
        ),
        "tags": ["high net worth", "liquidity risk", "tax"],
        "hashtags": (
            "#PrinciplesOfWealth #WealthPreservation #RiskManagement #FamilyOffice #Finance"
        ),
    },
    25: {
        "title": "Ray Dalio: Transferring Principles Across Life and Business | Principles of Wealth",
        "key_concept": "transferring wealth principles into life and business",
        "hook": (
            "People silo 'investing' from hiring, pricing, and cash in a company — then repeat "
            "the same ruin patterns with a different label.\n"
            "Macroeconomic reality is isomorphic: leverage, feedback, truth, and diversification "
            "show up in markets and in operating a firm.\n"
            "This episode is how Principles of Wealth transfers across the whole economic life, "
            "not just the brokerage login."
        ),
        "takeaways": [
            "portable principles outweigh siloed 'investor' identity in wealth building systems",
            "business cash and personal asset allocation adapt to the same rate and inflation regime",
            "theoretical principle-transfer with real hiring, pricing, and capital-budget cases",
            "avoiding ruin from being disciplined in SPY and reckless in operations",
            "resilient cross-domain rules you can audit in both books",
        ],
        "keywords": (
            "SEO: principles of wealth, macroeconomic framework, institutional capital strategies, "
            "and asset allocation models. Transferring principles across life and business is how "
            "operators stop living two incompatible risk cultures."
        ),
        "tags": ["business principles", "operators", "cash"],
        "hashtags": (
            "#PrinciplesOfWealth #BusinessStrategy #WealthBuilding #Operators #Macroeconomics"
        ),
    },
    26: {
        "title": "Ray Dalio: Mistakes That Only Appear at Higher Net Worths | Principles of Wealth",
        "key_concept": "mistakes that only appear at higher net worths",
        "hook": (
            "Early wealth mistakes are obvious: no savings, no plan, too much TSLA. Later "
            "mistakes are quieter: lifestyle inflation, concentration, bad advice, estate gaps.\n"
            "Macroeconomic reality is that a larger surface area creates new failure modes "
            "even if you 'already know how to invest.'\n"
            "This episode is a catalog of high-net-worth errors that look like success until they compound the wrong way."
        ),
        "takeaways": [
            "stage-appropriate controls outweigh leftover starter habits in capital preservation",
            "governance adapts when advisors, entities, and family complexity enter the picture",
            "theoretical wealth-stage models with real concentration, estate, and lifestyle cases",
            "avoiding ruin from mistakes that only become visible after a liquidity event",
            "resilient reviews designed for the problems you have now, not the problems you had at $40k",
        ],
        "keywords": (
            "Keyword set: long term wealth preservation, risk management investing, institutional "
            "capital strategies, and wealth building systems. Mistakes that only appear at higher "
            "net worths are how fortunes leak while the S&P 500 is still doing its job."
        ),
        "tags": ["HNWI mistakes", "estate", "lifestyle inflation"],
        "hashtags": (
            "#PrinciplesOfWealth #WealthPreservation #EstatePlanning #RiskManagement #Finance"
        ),
    },
    27: {
        "title": "Ray Dalio: The Role of Humility in Long-Term Wealth | Principles of Wealth",
        "key_concept": "humility as a long-term wealth technology",
        "hook": (
            "A winning Mag7 decade produces the most dangerous investor in the room: the one "
            "who thinks the tape confirmed their identity.\n"
            "Macroeconomic reality is mean reversion in luck, regimes, and factor leadership — "
            "humility is how you stay invited to the next cycle.\n"
            "This episode treats humility as risk management, not as a moral accessory."
        ),
        "takeaways": [
            "humility outweighs winner's narrative in risk management investing",
            "forecasts stay provisional when inflation, rates, and leadership rotate",
            "theoretical overconfidence research with real post-bull-market blowup patterns",
            "avoiding ruin from 'I cannot be wrong' sizing after a lucky decade",
            "resilient culture of being wrong fast without making it a personality crisis",
        ],
        "keywords": (
            "For investor psychology, long term wealth preservation, macroeconomic framework, "
            "and S&P 500 strategy. The role of humility in long-term wealth is why some books "
            "survive the regime that made them famous."
        ),
        "tags": ["humility", "overconfidence", "regime"],
        "hashtags": (
            "#PrinciplesOfWealth #InvestorPsychology #Humility #WealthBuilding #Investing"
        ),
    },
    28: {
        "title": "Ray Dalio: Teaching the Machine to the Next Generation | Principles of Wealth",
        "key_concept": "teaching the financial machine to the next generation",
        "hook": (
            "Heirs inherit accounts more often than they inherit decision rules — which is how "
            "a family goes from compounding to consumption in one emotional decade.\n"
            "Macroeconomic reality is that principles are the transferable asset; tickers are "
            "temporary expressions inside a rate and inflation regime.\n"
            "This episode is how to teach the machine so the next operator can run it without you."
        ),
        "takeaways": [
            "transferable principles outweigh ticker lists in multi-generation wealth preservation",
            "family investment policy adapts as tax, entities, and roles change",
            "theoretical stewardship with real IPS, trust, and education-cadence practice",
            "avoiding ruin from a generation that can spend but cannot decide",
            "resilient teaching loops: why, what, how, and when not to touch the engine",
        ],
        "keywords": (
            "SEO: principles of wealth, wealth building systems, institutional capital strategies, "
            "and long term wealth preservation. Teaching the machine to the next generation is "
            "estate planning for decision quality, not just for assets."
        ),
        "tags": ["family office", "next generation", "IPS"],
        "hashtags": (
            "#PrinciplesOfWealth #GenerationalWealth #FamilyOffice #WealthBuilding #Finance"
        ),
    },
    29: {
        "title": "Ray Dalio: Freedom, Time, and What Wealth Is Actually For | Principles of Wealth",
        "key_concept": "what wealth is actually for: freedom and time",
        "hook": (
            "Account size is a terrible north star — it turns life into a scoreboard and keeps "
            "people working a tape they already won, just to feel the number move.\n"
            "Macroeconomic reality is that purchasing power, optionality, and time are the "
            "output; the brokerage total is an input that inflation can quietly steal.\n"
            "This episode reconnects capital preservation to the reason you built the machine."
        ),
        "takeaways": [
            "optionality and time outweigh vanity AUM in a complete wealth building system",
            "real purchasing power adapts the goalposts when inflation redefines 'enough'",
            "theoretical utility of wealth with real burnout, overwork, and under-living cases",
            "avoiding ruin of the life the money was supposed to protect",
            "resilient definitions of enough that still respect market and policy risk",
        ],
        "keywords": (
            "Keyword cluster: long term wealth preservation, inflation protection strategies, "
            "principles of wealth, and macroeconomic framework. Freedom, time, and what wealth "
            "is actually for — without pretending markets stopped mattering."
        ),
        "tags": ["time freedom", "purchasing power", "enough"],
        "hashtags": (
            "#PrinciplesOfWealth #TimeFreedom #Inflation #WealthBuilding #PersonalFinance"
        ),
    },
    30: {
        "title": "Ray Dalio: Living Inside Reality — A Lifetime Practice | Principles of Wealth",
        "key_concept": "living inside economic reality as a lifetime practice",
        "hook": (
            "The last misconception is that you 'finish' money — that one correct S&P 500 "
            "strategy or one correct decade of Mag7 ownership graduates you from reality.\n"
            "Macroeconomic reality does not graduate anyone: inflation, policy, technology, "
            "and psychology keep moving the floor.\n"
            "This episode closes the series as a practice: stay inside what is true, keep "
            "the machine honest, and refuse the comfort of a finished story."
        ),
        "takeaways": [
            "ongoing contact with reality outweighs a one-time 'correct' portfolio",
            "principles stay alive as inflation, rates, and leadership regimes rotate",
            "theoretical lifetime practice with real review rituals used by serious allocators",
            "avoiding ruin from believing you are done with economic weather",
            "resilient identity as a student of markets rather than a prophet of them",
        ],
        "keywords": (
            "For principles of wealth, macroeconomic framework, wealth building systems, "
            "institutional capital strategies, and long term wealth preservation. Living inside "
            "reality is the lifetime practice that keeps every prior episode operational."
        ),
        "tags": ["lifetime practice", "reality", "stewardship"],
        "hashtags": (
            "#PrinciplesOfWealth #Macroeconomics #WealthBuilding #LifelongLearning #Investing"
        ),
    },
}


_SHORTS: dict[tuple[int, int], dict[str, Any]] = {
    (1, 1): {
        "title": "Why Cash Drag Still Hits SPY Holders | Principles of Wealth",
        "opening": (
            "Wealth is not the cash in your checking account, and it is not a green day in SPY — "
            "yet most households still treat both as the definition of being rich."
        ),
        "angle": (
            "When Treasury yields and money-market funds look 'safe,' cash drag quietly taxes "
            "purchasing power while Mag7 names and the S&P 500 keep repricing around inflation "
            "and the Fed path. Institutional capital does not confuse a paycheck or a cash pile "
            "with a wealth-building system; it measures claims on real assets, cash-flow engines, "
            "and the ability to survive a rate shock without becoming a forced seller."
        ),
        "tags": ["SPY", "cash drag", "Treasury yields"],
    },
    (1, 2): {
        "title": "The Feedback Rule Mag7 Investors Avoid | Principles of Wealth",
        "opening": (
            "The principle most people avoid is not a secret ticker — it is writing rules you "
            "will still obey when NVDA, AAPL, or META gap against you."
        ),
        "angle": (
            "Smart money in Mag7 leadership still runs feedback: what did the loss teach about "
            "sizing, liquidity, and correlation with QQQ? Retail treats a drawdown as humiliation "
            "and skips the post-mortem, which is how concentrated growth books become personality "
            "tests instead of S&P 500 strategy. Principles of Wealth treats avoided feedback as "
            "the real leak in long-term wealth preservation."
        ),
        "tags": ["Mag7", "NVDA", "feedback"],
    },
    (1, 3): {
        "title": "Pain Is Data: NVDA Drawdowns Train Capital | Principles of Wealth",
        "opening": (
            "Pain is data — a NVDA air-pocket or a QQQ flush is information about your risk "
            "budget, not a verdict on your identity."
        ),
        "angle": (
            "Institutional desks log volatility, correlation, and liquidity after growth-stock "
            "shocks; they do not rewrite their entire macroeconomic framework because one print "
            "hurt. Individual investors often reverse the process: they change the plan to stop "
            "the feeling, then re-enter Mag7 names at worse prices. Use the pain to tighten "
            "position sizing and market crash preparation, not to abandon compounding."
        ),
        "tags": ["NVDA", "QQQ", "drawdowns"],
    },
    (1, 4): {
        "title": "The Wealth Formula Institutions Still Use | Principles of Wealth",
        "opening": (
            "The wealth formula is not a hack for top growth stocks — it is a repeatable "
            "conversion of income, savings rate, and asset allocation into claims that survive inflation."
        ),
        "angle": (
            "Family offices and CIOs still run a boring equation: earn, retain, allocate, "
            "do not die. That formula holds whether the tape is obsessed with NVDA, the Fed, "
            "or the next CPI print. Speculative timing around high-yield products and options "
            "lotteries is what breaks the formula. Principles of Wealth maps the institutional "
            "version onto household asset allocation models."
        ),
        "tags": ["wealth formula", "asset allocation", "CPI"],
    },
    (1, 5): {
        "title": "Wealth Is Not Luck — Mag7 Timing Is a Trap | Principles of Wealth",
        "opening": (
            "Wealth is not luck, but a lucky Mag7 decade can convince you it was skill — until "
            "the first rate cycle that does not cooperate."
        ),
        "angle": (
            "AAPL, MSFT, NVDA, and the rest of the Magnificent Seven minted a generation of "
            "accidental geniuses. Institutional capital still separates process from outcome: "
            "they stress-test S&P 500 strategy against inflation protection and interest-rate "
            "impact on stocks. If your plan only works when leadership is narrow and yields are "
            "friendly, you do not have a wealth building system — you have a weather report."
        ),
        "tags": ["Mag7", "AAPL", "MSFT"],
    },
    (1, 6): {
        "title": "Clarity Beats Confidence When Yields Jump | Principles of Wealth",
        "opening": (
            "Clarity beats confidence when Treasury yields reprice duration, housing, and "
            "growth stocks in the same week — confidence without a map is just volume."
        ),
        "angle": (
            "FOMC days reward people who already knew their duration, cash, and equity mix; "
            "they punish people who were loud on social media about 'can't miss' top growth "
            "stocks. Interest-rate impact on stocks is not a vibe. Principles of Wealth treats "
            "clarity — what you own, why, and what would falsify it — as the real edge versus "
            "performative conviction."
        ),
        "tags": ["Treasury yields", "FOMC", "duration"],
    },
    (1, 7): {
        "title": "Principles Beat Rules When the Fed Shifts | Principles of Wealth",
        "opening": (
            "Rules say 'always buy the dip in SPY.' Principles ask whether this dip is a "
            "liquidity event, a rate shock, or a gift — and they let you update without shame."
        ),
        "angle": (
            "When the Fed path, QT, and inflation data shift, brittle rules become ruin. "
            "Institutional capital runs principles: survive first, then participate in S&P 500 "
            "strategy, then optimize. That is why a 60/40 book, a cash buffer, and a written "
            "policy still matter when the tape is screaming about one ticker. Principles versus "
            "rules is the difference between a machine and a superstition."
        ),
        "tags": ["Fed", "SPY", "60/40"],
    },
    (1, 8): {
        "title": "IQ Won't Save You From a Treasury Shock | Principles of Wealth",
        "opening": (
            "Intelligence is not enough — 2022 taught highly paid professionals that TLT, "
            "duration, and 'safe' bonds can inflict the same pain as a growth-stock crash."
        ),
        "angle": (
            "IQ did not protect Silicon Valley depositors, duration-heavy pensions, or "
            "overconfident Mag7 timers from a rate regime they had never lived. Market crash "
            "preparation is a design problem: liquidity, leverage, and correlation. Principles "
            "of Wealth puts risk management investing above clever narratives so a Treasury "
            "shock cannot delete the compounding clock."
        ),
        "tags": ["TLT", "Treasuries", "2022"],
    },
    (1, 9): {
        "title": "Avoiding Ruin Beats Chasing High-Yield Stocks | Principles of Wealth",
        "opening": (
            "Avoiding ruin is the game — chasing high-yield stocks, HYG coupons, and levered "
            "products is how people exit the game while feeling productive."
        ),
        "angle": (
            "Every late cycle, retail rotates from Mag7 FOMO into yield theater: covered-call "
            "ETFs, junk credit, and 'income' that is just return of capital. Institutional "
            "books still rank survival above extra yield because a 50% hole is not a 50% "
            "opportunity — it is a math problem most never solve. Capital preservation is "
            "the high-RPM skill; speculation is the entertainment product."
        ),
        "tags": ["high yield", "HYG", "ruin"],
    },
    (2, 1): {
        "title": "Pain Is Data After a QQQ Flush | Principles of Wealth",
        "opening": (
            "Pain is data after a QQQ flush — the loss is a report on sizing and liquidity, "
            "not a reason to delete the brokerage app."
        ),
        "angle": (
            "Nasdaq leadership can train you or it can traumatize you. The difference is whether "
            "you log the drawdown against your S&P 500 strategy and risk budget, or you treat "
            "it as a referendum on your intelligence. Institutional capital already knows "
            "volatility is tuition. Principles of Wealth shows how to collect it without paying twice."
        ),
        "tags": ["QQQ", "Nasdaq", "pain is data"],
    },
    (2, 2): {
        "title": "Why Losses Teach Faster Than Mag7 Wins | Principles of Wealth",
        "opening": (
            "Losses teach faster than wins because a green NVDA year can hide a broken process "
            "while a red month exposes it immediately."
        ),
        "angle": (
            "Bull-market talent is mostly untested process. Credit-spread spikes, CPI surprises, "
            "and growth-stock air-pockets are the exams. Wealth building systems that only "
            "study winners will fail market crash preparation. Use the loss to locate leverage, "
            "concentration, and emotional override — the same post-trade review a professional desk runs."
        ),
        "tags": ["NVDA", "losses", "CPI"],
    },
    (2, 3): {
        "title": "Avoiding Pain Creates Bigger Risk in SPY | Principles of Wealth",
        "opening": (
            "Avoiding pain creates bigger risk: skipping statements, disabling alerts, and "
            "refusing to rebalance SPY are how small problems become ruin."
        ),
        "angle": (
            "Investors who cannot look at a drawdown cannot manage interest-rate impact on "
            "stocks, inflation protection, or contribution policy. Institutional books schedule "
            "the pain — reviews, stress tests, red-team questions. Emotional avoidance is not "
            "self-care in markets; it is how 2022-style duration and Nasdaq hits become identity crises."
        ),
        "tags": ["SPY", "avoidance", "rebalancing"],
    },
    (2, 4): {
        "title": "Feedback Loops Beat Hot Takes on NVDA | Principles of Wealth",
        "opening": (
            "Feedback loops build judgment; hot takes on NVDA build an audience. Only one of "
            "those compounds capital."
        ),
        "angle": (
            "A loop is: thesis, position, outcome, written lesson, updated size. That is how "
            "institutional capital strategies stay honest through Mag7 rotations. A take is: "
            "announce, double down, mute critics. Principles of Wealth treats judgment as a "
            "manufactured asset — slower than a viral ticker call, more valuable than one."
        ),
        "tags": ["feedback loops", "NVDA", "judgment"],
    },
    (2, 5): {
        "title": "Mistakes Are Not the Enemy. Ruin Is. | Principles of Wealth",
        "opening": (
            "Mistakes are not the enemy of a wealth building system — unprocessed mistakes and "
            "levered mistakes are."
        ),
        "angle": (
            "Every allocator will be wrong about inflation timing, Fed cuts, and which top "
            "growth stocks lead. The professional difference is bounding the mistake so it "
            "cannot become ruin: position size, liquidity, and a rule that forbids revenge "
            "adds. Market crash preparation starts before the crash, in how you treat ordinary errors."
        ),
        "tags": ["mistakes", "position size", "ruin"],
    },
    (2, 6): {
        "title": "Emotion vs Information on FOMC Day | Principles of Wealth",
        "opening": (
            "FOMC day is where emotion masquerades as information — the statement is data; "
            "your heart rate is not a trading signal."
        ),
        "angle": (
            "Rates, dots, and QT language can reprice SPY, TLT, and Mag7 in minutes. "
            "Institutional capital has a playbook for that volatility. Retail often has a "
            "mood. Separate the information (policy path, inflation fight) from the feeling "
            "(need to act). That split is core risk management investing."
        ),
        "tags": ["FOMC", "TLT", "emotion"],
    },
    (2, 7): {
        "title": "Why Being Wrong on TSLA Can Be Valuable | Principles of Wealth",
        "opening": (
            "Being wrong on TSLA — or any high-beta name — is valuable if it updates your "
            "map of sentiment, liquidity, and your own override habits."
        ),
        "angle": (
            "A wrong call that produces a written principle is cheaper tuition than a right "
            "call that produces arrogance. High-beta names like Tesla punish emotional capital "
            "because they mix narrative, options flow, and macro beta. Use the miss to improve "
            "sizing rules, not to hunt a get-even trade that converts risk into ruin."
        ),
        "tags": ["TSLA", "high beta", "being wrong"],
    },
    (2, 8): {
        "title": "The Cost of Ignoring Credit-Spread Feedback | Principles of Wealth",
        "opening": (
            "Ignoring feedback is how HYG, regional banks, and 'nothing to see here' credit "
            "stories become overnight insolvency events."
        ),
        "angle": (
            "Credit spreads, deposit flight, and funding stress are feedback the tape sends "
            "before equity headlines catch up — SVB was not a surprise to people watching "
            "the plumbing. Institutional capital reads that channel. Long term wealth "
            "preservation requires you to as well, even if your book is mostly SPY."
        ),
        "tags": ["credit spreads", "HYG", "SVB"],
    },
    (2, 9): {
        "title": "Learning Speed Is the Edge After CPI | Principles of Wealth",
        "opening": (
            "Learning speed is the advantage after a CPI surprise — not the speed of your "
            "market order."
        ),
        "angle": (
            "The print hits, Mag7 gaps, yields jump, and the feed fills with certainty. "
            "The edge is how fast you update probabilities without blowing up the plan: "
            "inflation protection, duration, and equity beta. Wealth building systems that "
            "learn weekly outperform systems that only feel weekly. That is institutional tempo."
        ),
        "tags": ["CPI", "inflation", "learning speed"],
    },
    (2, 10): {
        "title": "Pain Refines Strategy. Panic Deletes It. | Principles of Wealth",
        "opening": (
            "Pain refines strategy when it is processed; panic deletes strategy when it is "
            "used as a reason to sell the S&P 500 engine at the lows."
        ),
        "angle": (
            "Drawdowns in QQQ, NVDA, or a 60/40 book are the gym, not the funeral — unless "
            "you walk out mid-set. Principles of Wealth treats refined strategy as the output "
            "of survived pain: tighter risk budgets, clearer liquidity, fewer ego trades. "
            "Panic is just pain without a notebook."
        ),
        "tags": ["strategy", "QQQ", "panic"],
    },
    (3, 1): {
        "title": "The Radical Truth About Money vs Mag7 Myths | Principles of Wealth",
        "opening": (
            "The radical truth about money is that Mag7 ownership does not make a fragile "
            "personal economy healthy — cash flow and structure still decide."
        ),
        "angle": (
            "AAPL, MSFT, GOOGL, AMZN, META, NVDA, and TSLA can be excellent businesses and "
            "still be a terrible substitute for seeing your own leverage, savings rate, and "
            "duration. Radical truth is forensic. Narrative is cheap. Principles of Wealth "
            "uses mega-cap gravity as a teaching tool, not as a personality."
        ),
        "tags": ["Mag7", "radical truth", "cash flow"],
    },
    (3, 2): {
        "title": "Self-Deception Is Expensive After Earnings | Principles of Wealth",
        "opening": (
            "Self-deception is expensive after earnings — 'it's different this time' around "
            "a miss at a favorite ticker is how thesis-defense becomes a wealth tax."
        ),
        "angle": (
            "Quality compounders still miss, guide down, and reprice. Institutional capital "
            "updates. Retail often bargains with the number. Inflation, rates, and competition "
            "do not care about your attachment to a logo. Radical honesty about earnings "
            "quality is core stock market trend analysis."
        ),
        "tags": ["earnings", "self-deception", "guidance"],
    },
    (3, 3): {
        "title": "Optimism vs Clarity When CPI Hits | Principles of Wealth",
        "opening": (
            "Optimism vs clarity is the CPI-week split: one hopes the print 'should' be fine; "
            "the other already knows what the book does if it is not."
        ),
        "angle": (
            "Inflation protection strategies are built in calm, not as a tweet during the "
            "release. Clarity means knowing duration, commodities, cash, and equity beta "
            "before the number. Optimism is not a hedge. Principles of Wealth prefers maps "
            "to mood, especially when Treasury yields and Mag7 beta disagree."
        ),
        "tags": ["CPI", "optimism", "inflation"],
    },
    (3, 4): {
        "title": "Transparency Builds Wealth. Spin Destroys It. | Principles of Wealth",
        "opening": (
            "Transparency builds wealth because you cannot allocate what you refuse to measure — "
            "spin is how households and companies both go insolvent while sounding confident."
        ),
        "angle": (
            "Public companies that hide the ball eventually meet credit markets. Households "
            "that hide net-worth math from themselves meet the same wall at a smaller scale. "
            "Institutional capital strategies demand look-through: fees, leverage, liquidity. "
            "Make your own books that honest."
        ),
        "tags": ["transparency", "fees", "look through"],
    },
    (3, 5): {
        "title": "Why Reality Always Wins Over Stock Stories | Principles of Wealth",
        "opening": (
            "Reality always wins — a story about top growth stocks is not a cash-flow statement, "
            "and a narrative about 'the Fed has to cut' is not a rate path."
        ),
        "angle": (
            "Markets can stay wrong longer than your pride, but not longer than solvency. "
            "Economic reality — inflation, policy, earnings, and plumbing — eventually "
            "reprices the story. Long term wealth preservation is the practice of living on "
            "the reality side of that lag."
        ),
        "tags": ["economic reality", "Fed cuts", "stories"],
    },
    (3, 6): {
        "title": "The Cost of Financial Illusions in 2026 | Principles of Wealth",
        "opening": (
            "Financial illusions are expensive in any year — 2026's mix of Mag7 concentration, "
            "fiscal headlines, and yield noise just makes the costumes more convincing."
        ),
        "angle": (
            "Illusions include: cash is risk-free after inflation, one ticker is a retirement, "
            "and a high coupon is a strategy. Asset allocation models exist to puncture those. "
            "Principles of Wealth prices the cost of the illusion before the tape does it for you."
        ),
        "tags": ["illusions", "2026", "concentration"],
    },
    (3, 7): {
        "title": "Confidence Without Clarity Is Hidden Leverage | Principles of Wealth",
        "opening": (
            "Confidence without clarity is hidden leverage — you are borrowing against a "
            "future you have not actually underwritten."
        ),
        "angle": (
            "That is how people size like a hedge fund with a household balance sheet, then "
            "meet a VIX spike or a payroll miss. Risk management investing starts with "
            "knowing what would prove you wrong. Loud confidence is not a macroeconomic framework."
        ),
        "tags": ["confidence", "VIX", "leverage"],
    },
    (3, 8): {
        "title": "Radical Truth Removes Emotion From SPY | Principles of Wealth",
        "opening": (
            "Radical truth removes emotion from SPY decisions — the index is a tool, not a "
            "parent, not an enemy, not a personality."
        ),
        "angle": (
            "Once the S&P 500 is a mechanism for owning US cash-flow at a price, you can "
            "talk about valuation, rates, and concentration without treating every session as "
            "a moral event. That emotional distance is how institutional books rebalance. "
            "It is also how you stop arguing with a chart."
        ),
        "tags": ["SPY", "radical truth", "rebalance"],
    },
    (3, 9): {
        "title": "Facing Reality Early Beats a Forced Sale | Principles of Wealth",
        "opening": (
            "Facing reality early saves money because delayed honesty becomes a forced sale — "
            "in a stock, a house, or a business — at the worst bid."
        ),
        "angle": (
            "Credit events, duration losses, and failing theses get cheaper the sooner they "
            "are named. Market crash preparation is mostly naming. Principles of Wealth treats "
            "early truth as a liquidity strategy, not as pessimism."
        ),
        "tags": ["forced sale", "liquidity", "honesty"],
    },
    (3, 10): {
        "title": "Truth Creates Resilience When Credit Tightens | Principles of Wealth",
        "opening": (
            "Truth creates financial resilience when credit tightens — you already know your "
            "covenants, cash runway, and what you will sell first."
        ),
        "angle": (
            "Spreads, bank lending standards, and 'risk-off' in HYG do not create the problem; "
            "they reveal it. Households and firms that practiced radical truth already have "
            "the list. That is institutional capital behavior scaled down to a kitchen table."
        ),
        "tags": ["credit tightening", "HYG", "resilience"],
    },
    (4, 1): {
        "title": "Why High Salaries Didn't Save Silicon Valley Bank | Principles of Wealth",
        "opening": (
            "High salaries did not save Silicon Valley Bank depositors — income is not a hedge "
            "against duration, concentration, and a run."
        ),
        "angle": (
            "SVB is the cleanest recent lesson that compensation, prestige, and 'smart people' "
            "do not replace asset-liability design. Treasuries marked at a loss, uninsured "
            "deposits, and correlated clients turned a regional bank into a macroeconomic "
            "event. Principles of Wealth uses it as plumbing, not as a dunk on tech."
        ),
        "tags": ["SVB", "Silicon Valley", "duration"],
    },
    (4, 2): {
        "title": "FTX Proved Hard Work Doesn't Create Wealth | Principles of Wealth",
        "opening": (
            "FTX proved hard work does not create wealth — structure, custody, and truth do. "
            "Hustle inside a broken vehicle is just a faster way to the same hole."
        ),
        "angle": (
            "Crypto market structure, commingled assets, and celebrity confidence produced a "
            "failure that looked like innovation until the bid disappeared. Institutional "
            "capital already knew custody and governance were the product. Effort was never "
            "the missing ingredient. Economic reality was."
        ),
        "tags": ["FTX", "crypto", "custody"],
    },
    (4, 3): {
        "title": "The Assumptions That Broke Tech Stocks in 2022 | Principles of Wealth",
        "opening": (
            "The assumptions that broke tech stocks in 2022 were duration, zero-rate discounting, "
            "and the idea that growth is immune to the cost of capital."
        ),
        "angle": (
            "When Treasury yields rose, long-duration cash-flow stories in Nasdaq repriced. "
            "That was not a morality play about 'tech being fake'; it was interest-rate impact "
            "on stocks working as designed. S&P 500 strategy that ignored duration learned "
            "an expensive lesson. Keep the lesson; skip the superstition."
        ),
        "tags": ["2022", "tech stocks", "duration"],
    },
    (4, 4): {
        "title": "Why the S&P 500 Survives When Companies Fail | Principles of Wealth",
        "opening": (
            "The S&P 500 survives when companies fail because it is a system of replacement — "
            "your concentrated stock is not."
        ),
        "angle": (
            "Index mechanics, creative destruction, and sector rotation are why SPY can live "
            "through bankruptcies that wipe out individual holders. That is not a reason to "
            "be reckless; it is a reason to understand what you actually own. Asset allocation "
            "models should respect the difference between a market and a name."
        ),
        "tags": ["S&P 500", "SPY", "creative destruction"],
    },
    (4, 5): {
        "title": "How Credit Suisse Lost Trust Before Capital | Principles of Wealth",
        "opening": (
            "Credit Suisse lost trust before it lost the headline — reputational and funding "
            "stress are leading indicators, not lagging gossip."
        ),
        "angle": (
            "A global bank can look capitalized on a slide and still be dead in the funding "
            "market. That is economic reality: trust is liquidity. Households meet a smaller "
            "version when counterparties, brokers, or 'safe' yield products wobble. Watch "
            "plumbing, not just the logo."
        ),
        "tags": ["Credit Suisse", "trust", "funding"],
    },
    (4, 6): {
        "title": "Why 'Safe' Bonds Became Dangerous in 2023 | Principles of Wealth",
        "opening": (
            "Why 'safe' bonds became dangerous in 2023: duration is a risk factor, not a "
            "personality, and TLT taught that in public."
        ),
        "angle": (
            "Inflation protection is not 'own bonds because they are conservative.' It is "
            "matching liabilities, understanding duration, and not using long Treasuries as "
            "a sleep aid in a hiking cycle. 2022–2023 rewrote the 60/40 brochure. Update the "
            "brochure; keep the need for ballast that actually ballasts."
        ),
        "tags": ["TLT", "bonds", "duration risk"],
    },
    (4, 7): {
        "title": "NVDA and the Risk of One-Stock Wealth | Principles of Wealth",
        "opening": (
            "NVDA and the risk of one-stock wealth: a spectacular business can still be an "
            "unacceptable percentage of a human life."
        ),
        "angle": (
            "Concentration risk is not a take on NVIDIA's products. It is math: correlation, "
            "liquidity, tax, and the recovery time if the multiple compresses. Institutional "
            "books have risk limits for a reason. Top growth stocks can be owned without "
            "becoming the entire identity of the household."
        ),
        "tags": ["NVDA", "concentration", "NVIDIA"],
    },
    (4, 8): {
        "title": "The Dollar Is a System, Not a Guarantee | Principles of Wealth",
        "opening": (
            "The dollar is a system — reserve status, funding markets, and policy — not a "
            "guarantee that cash under the mattress beats inflation."
        ),
        "angle": (
            "DXY moves, Treasury supply, and global dollar demand are macroeconomic framework, "
            "not patriotism. Purchasing-power risk is why wealth building systems own claims "
            "on real cash flow instead of worshipping the unit of account. Respect the system; "
            "do not confuse it with a personal hedge."
        ),
        "tags": ["US dollar", "DXY", "reserves"],
    },
    (4, 9): {
        "title": "Crypto Didn't Fail — Its Structure Did | Principles of Wealth",
        "opening": (
            "Crypto did not fail as an idea in every case — specific structures failed: "
            "custody, leverage, and truth. That distinction is how you stop overlearning the wrong lesson."
        ),
        "angle": (
            "BTC and ETH as volatile assets are one discussion. Platforms that mixed customer "
            "assets, hidden leverage, and celebrity trust are another. Market crash preparation "
            "in digital assets is mostly structure. Principles of Wealth keeps that forensic."
        ),
        "tags": ["BTC", "ETH", "crypto structure"],
    },
    (4, 10): {
        "title": "What Survives Every Market Cycle in SPY | Principles of Wealth",
        "opening": (
            "What actually survives every market cycle is not a ticker call — it is solvency, "
            "adaptability, and a claim on real economic function."
        ),
        "angle": (
            "Companies fail. Factors rotate. Mag7 leadership is not a law of physics. The S&P 500 "
            "as a replenishing system, cash-flow quality, and low ruin-risk are closer to "
            "survivors. Build for that, not for the logo that is winning this quarter."
        ),
        "tags": ["market cycles", "SPY", "survivorship"],
    },
    (5, 6): {
        "title": "Why Tesla Punished Emotional Investors | Principles of Wealth",
        "opening": (
            "Tesla punished emotional investors because TSLA is a sentiment, options, and "
            "macro-beta cocktail — not a calm compounding instrument."
        ),
        "angle": (
            "High-beta names convert ego into P&L faster than boring cash-flow stocks. That "
            "makes them excellent teachers and terrible identity anchors. Risk management "
            "investing means sizing Tesla-like volatility so a narrative swing cannot become "
            "household ruin. Discipline is not disloyalty to a product you like."
        ),
        "tags": ["TSLA", "Tesla", "sentiment"],
    },
    (5, 7): {
        "title": "How Apple Models Capital Discipline | Principles of Wealth",
        "opening": (
            "Apple shows what discipline looks like at corporate scale: buybacks, cash, and "
            "a refusal to confuse a hit product with an unbounded risk budget."
        ),
        "angle": (
            "AAPL is not a personality cult in this framing — it is a case study in capital "
            "return, fortress liquidity, and operating cadence. Households can steal the "
            "pattern without pretending they are a trillion-dollar treasury. Institutional "
            "capital already did. Ego is the part that refuses to copy the boring pieces."
        ),
        "tags": ["AAPL", "Apple", "buybacks"],
    },
    (5, 8): {
        "title": "Why Crypto Crashes Destroy Emotional Capital | Principles of Wealth",
        "opening": (
            "Crypto collapses destroy emotional capital because the loss is public, identity-"
            "coded, and easy to revenge-trade — which is how a market event becomes a character event."
        ),
        "angle": (
            "BTC drawdowns, exchange failures, and leverage flushes are volatility plus shame. "
            "The shame is the expensive part: it produces all-in rebuilds. Principles of Wealth "
            "treats digital-asset pain as data for structure and sizing, not as a referendum "
            "on whether you are 'early' or 'stupid.'"
        ),
        "tags": ["BTC", "crypto crash", "leverage"],
    },
    (5, 9): {
        "title": "What Hedge Funds Do That Individuals Ignore | Principles of Wealth",
        "opening": (
            "What hedge funds do that individuals ignore is not magic alpha — it is risk books, "
            "limits, and the humility to be small when the tape is not theirs."
        ),
        "angle": (
            "Gross exposure, stop-outs, and factor hedging are unglamorous. Retail copies the "
            "ticker and skips the governor. That is why institutional capital strategies look "
            "boring until a crash. Steal the governors. Leave the lore."
        ),
        "tags": ["hedge funds", "risk limits", "exposure"],
    },
    (5, 10): {
        "title": "The Rule That Keeps Wealth Alive in Crises | Principles of Wealth",
        "opening": (
            "The rule that keeps wealth alive in crises is simple: never take a risk that can "
            "remove you from the game — then keep running the machine through the noise."
        ),
        "angle": (
            "Market crash preparation is mostly pre-commitment: cash, leverage caps, and a "
            "written policy for SPY, credit, and your job risk. Ego wants a heroic call. "
            "Survival wants a boring rule you already agreed to. That is the silent tax you "
            "stop paying when ego is no longer the CIO."
        ),
        "tags": ["crisis", "ruin", "precommitment"],
    },
}


PLAYLIST_META: dict[int, dict[str, str]] = {
    1: {
        "title": "Principles of Wealth: ACT I — Visualizing Financial Reality",
        "theme": (
            "ACT I visualizes financial reality: principles, feedback, truth, economic plumbing, "
            "ego, ruin, stability, compounding, decisions, and designed money rules."
        ),
    },
    2: {
        "title": "Principles of Wealth: ACT II — Mechanics of the Financial Machine",
        "theme": (
            "ACT II opens the machine: cash flow, leverage, debt, diversification, patience, "
            "automation, simplicity, scaling, and when to change the system."
        ),
    },
    3: {
        "title": "Principles of Wealth: ACT III — Psychology of Lasting Wealth",
        "theme": (
            "ACT III is the psychology of lasting wealth: cycles, drawdowns, large numbers, "
            "fear, transfer, late-stage mistakes, humility, succession, purpose, and practice."
        ),
    },
}


def long_pack(episode: int) -> dict[str, Any]:
    row = _LONGS[int(episode)]
    title = _fit_title(str(row["title"]))
    description = render_long_description(
        hook=str(row["hook"]),
        key_concept=str(row["key_concept"]),
        takeaways=list(row["takeaways"]),
        keywords=str(row["keywords"]),
        hashtags=str(row.get("hashtags") or LONG_HASHTAGS),
    )
    tags = _merge_tags(BASE_TAGS, list(row.get("tags") or []))
    return {
        "episode": int(episode),
        "title": title,
        "description": description,
        "tags": tags,
        "key_concept": row["key_concept"],
    }


def _synthesize_short(episode: int, clip: int, hook: str) -> dict[str, Any]:
    trend = EPISODE_TRENDS.get(int(episode), "SPY, Treasury yields, and Mag7 leadership")
    core = (hook or f"Episode {episode} capital principle").strip()
    core = core.split("|")[0].strip()
    title = _fit_title(f"{core} — {trend.split(',')[0].strip()} | Principles of Wealth")
    opening = (
        f"{core}. That idea sits inside today's tape: {trend}. Retail treats it as a slogan; "
        "institutional capital treats it as a constraint on sizing, liquidity, and ruin."
    )
    angle = (
        f"As {trend} reprice risk, individual investors often swap a written S&P 500 strategy "
        "for short-term noise around top growth stocks and headline yields. The Principles of "
        "Wealth model keeps the principle — risk asymmetry, inflation protection, and long-term "
        "wealth preservation — and refuses to let a single session rewrite the machine."
    )
    return {"title": title, "opening": opening, "angle": angle, "tags": [f"ep{episode}", "shorts"]}


def short_pack(
    episode: int,
    clip: int,
    *,
    hook: str = "",
    long_video_id: str = "",
) -> dict[str, Any]:
    row = _SHORTS.get((int(episode), int(clip)))
    if row is None:
        row = _synthesize_short(episode, clip, hook)
    title = _fit_title(str(row["title"]))
    description = render_short_description(
        opening=str(row["opening"]),
        angle=str(row["angle"]),
        long_video_id=long_video_id,
    )
    tags = _merge_tags(BASE_TAGS, ["shorts"], list(row.get("tags") or []))
    return {
        "episode": int(episode),
        "clip": int(clip),
        "title": title,
        "description": description,
        "tags": tags,
    }


def playlist_pack(act: int, episode_titles: list[tuple[int, str]]) -> dict[str, str]:
    meta = PLAYLIST_META[int(act)]
    lines = [f"Ep {n}: {title}" for n, title in episode_titles]
    return {
        "title": meta["title"],
        "description": render_playlist_description(theme=meta["theme"], episode_lines=lines),
    }


def export_pack() -> dict[str, Any]:
    longs = {str(n): long_pack(n) for n in range(1, 31)}
    shorts = {
        f"{ep}.{clip}": short_pack(ep, clip)
        for (ep, clip) in sorted(_SHORTS)
    }
    playlists = {}
    ranges = {1: range(1, 11), 2: range(11, 21), 3: range(21, 31)}
    for act, ep_range in ranges.items():
        titles = [(n, long_pack(n)["title"]) for n in ep_range]
        playlists[str(act)] = playlist_pack(act, titles)
    return {
        "channel": CHANNEL_BRAND,
        "disclaimer": DISCLAIMER,
        "longs": longs,
        "shorts": shorts,
        "playlists": playlists,
    }


def validate_pack() -> list[str]:
    errors: list[str] = []
    for n in range(1, 31):
        pack = long_pack(n)
        if len(pack["title"]) > _YT_TITLE_MAX:
            errors.append(f"long {n} title {len(pack['title'])}: {pack['title']}")
        if len(pack["description"]) > 5000:
            errors.append(f"long {n} description {len(pack['description'])}")
    for key, row in _SHORTS.items():
        pack = short_pack(key[0], key[1])
        if len(pack["title"]) > _YT_TITLE_MAX:
            errors.append(f"short {key} title {len(pack['title'])}: {pack['title']}")
        if len(pack["description"]) > 5000:
            errors.append(f"short {key} description {len(pack['description'])}")
    return errors
