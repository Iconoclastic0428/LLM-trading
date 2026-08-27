from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import PercentFormatter


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HISTORY_DIR = ROOT / "history"
PERFORMANCE_DIR = ROOT / "performance"
YEARS_DIR = ROOT / "years"

COLORS = {
    "strategy": "#1565C0",
    "spy": "#607D8B",
    "qqq": "#7B1FA2",
    "zero": "#263238",
}

ROLE_LABELS = {
    "sealed_year": "Sealed test year",
    "unrevealed_retrospective_holdout": "Unrevealed retrospective holdout",
    "training_boundary_partial": "Partial training boundary",
    "training_fit": "Training fit",
    "training_with_validation_boundary": "Training with validation boundary",
    "validation_check": "Validation check",
    "mixed_unrevealed_and_forward": "Mixed retrospective holdout and forward period",
}


def percent(value: float) -> str:
    return f"{value:+.2%}"


def role_label(status: str) -> str:
    return ROLE_LABELS.get(status, status.replace("_", " ").title())


def write_lf(path: Path, content: str) -> None:
    """Write deterministic UTF-8 text without platform-specific newlines."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def configure_plot() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "#FAFAFA",
            "axes.edgecolor": "#B0BEC5",
            "axes.grid": True,
            "grid.alpha": 0.24,
            "grid.color": "#78909C",
            "font.size": 11,
            "axes.titleweight": "bold",
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
        }
    )


def year_curve(daily: pd.DataFrame, annual: pd.Series) -> pd.DataFrame:
    year = int(annual["year"])
    mask = daily["date"].dt.year.eq(year)
    indexes = daily.index[mask]
    if indexes.empty:
        raise ValueError(f"no daily performance rows for {year}")
    final = daily.loc[indexes[-1]]
    # Annual accounting starts at the first session's open, while the global
    # wealth series carries the prior close across the year boundary. Infer the
    # opening baseline from the audited annual return so each year chart ends
    # at the exact published annual result.
    base = {
        "strategy": float(final["strategy_equity"]) / (1.0 + float(annual["strategy_return"])),
        "spy": float(final["spy_equity"]) / (1.0 + float(annual["spy_return"])),
        "qqq": float(final["qqq_equity"]) / (1.0 + float(annual["qqq_return"])),
    }
    frame = daily.loc[indexes].copy()
    frame["strategy_return_index"] = frame["strategy_equity"] / base["strategy"] - 1.0
    frame["spy_return_index"] = frame["spy_equity"] / base["spy"] - 1.0
    frame["qqq_return_index"] = frame["qqq_equity"] / base["qqq"] - 1.0
    return frame


def render_year_chart(frame: pd.DataFrame, annual: pd.Series, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 8), dpi=120)
    ax.plot(frame["date"], frame["strategy_return_index"], color=COLORS["strategy"], linewidth=2.5, label="Old 5 strategy")
    ax.plot(frame["date"], frame["qqq_return_index"], color=COLORS["qqq"], linewidth=2.0, label="QQQ")
    ax.plot(frame["date"], frame["spy_return_index"], color=COLORS["spy"], linewidth=1.8, label="SPY")
    ax.axhline(0.0, color=COLORS["zero"], linewidth=0.8, alpha=0.65)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_ylabel("Return from start of year")
    ax.set_xlabel("Session")
    fig.suptitle(
        f"Old 5 strategy vs QQQ and SPY — {int(annual['year'])}",
        fontsize=16,
        fontweight="bold",
        y=0.98,
    )
    ax.set_title(
        f"{role_label(str(annual['status']))}  |  Strategy {percent(float(annual['strategy_return']))}  "
        f"QQQ {percent(float(annual['qqq_return']))}  SPY {percent(float(annual['spy_return']))}",
        color="#455A64",
        fontsize=10,
        pad=12,
    )
    ax.legend(loc="best", frameon=True)
    fig.autofmt_xdate()
    fig.subplots_adjust(top=0.89)
    fig.savefig(output, metadata={"Software": "old5_stock_strategy/scripts/render_reports.py"})
    plt.close(fig)


def render_cumulative_chart(daily: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(15, 8), dpi=120)
    ax.plot(daily["date"], daily["strategy_equity"], color=COLORS["strategy"], linewidth=2.4, label="Old 5 strategy")
    ax.plot(daily["date"], daily["qqq_equity"], color=COLORS["qqq"], linewidth=2.0, label="QQQ")
    ax.plot(daily["date"], daily["spy_equity"], color=COLORS["spy"], linewidth=1.8, label="SPY")
    ax.set_yscale("log")
    ax.set_ylabel("Growth of $1 (log scale)")
    ax.set_xlabel("Session")
    ax.set_title("Old 5 strategy — cumulative real-500 replay")
    ax.legend(loc="upper left", frameon=True)
    fig.savefig(output, metadata={"Software": "old5_stock_strategy/scripts/render_reports.py"})
    plt.close(fig)


def render_annual_returns(annual: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(17, 8), dpi=120)
    x = list(range(len(annual)))
    width = 0.26
    ax.bar([v - width for v in x], annual["strategy_return"], width, color=COLORS["strategy"], label="Old 5 strategy")
    ax.bar(x, annual["qqq_return"], width, color=COLORS["qqq"], label="QQQ")
    ax.bar([v + width for v in x], annual["spy_return"], width, color=COLORS["spy"], label="SPY")
    ax.axhline(0.0, color=COLORS["zero"], linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(annual["year"].astype(str), rotation=55, ha="right")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_ylabel("Calendar-year return")
    ax.set_title("Old 5 strategy — annual returns by experimental role")
    ax.legend(loc="upper left", ncols=3, frameon=True)
    fig.savefig(output, metadata={"Software": "old5_stock_strategy/scripts/render_reports.py"})
    plt.close(fig)


def annual_table(annual: pd.DataFrame, link_years: bool = True) -> list[str]:
    lines = [
        "| Year | Experimental role | Strategy | QQQ | SPY | Active vs QQQ |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in annual.itertuples(index=False):
        year_text = f"[{row.year}](../years/{row.year}/)" if link_years else str(row.year)
        lines.append(
            f"| {year_text} | {role_label(row.status)} | {percent(row.strategy_return)} | "
            f"{percent(row.qqq_return)} | {percent(row.spy_return)} | {percent(row.active_vs_qqq)} |"
        )
    return lines


def write_year_readme(year_dir: Path, annual: pd.Series, weekly: pd.DataFrame) -> None:
    flags = (
        f"training={annual['training_set']}, validation={annual['validation_set']}, "
        f"original sealed test={annual['original_sealed_test']}"
    )
    lines = [
        f"# {int(annual['year'])}: {role_label(str(annual['status']))}",
        "",
        f"- Performance interval: **{annual['first_date']} through {annual['last_date']}** ({int(annual['sessions'])} sessions)",
        f"- Experimental flags: **{flags}**",
        f"- Old 5 return: **{percent(float(annual['strategy_return']))}**",
        f"- QQQ return: **{percent(float(annual['qqq_return']))}**",
        f"- SPY return: **{percent(float(annual['spy_return']))}**",
        f"- Active return versus QQQ: **{percent(float(annual['active_vs_qqq']))}**",
        f"- Weekly holding snapshots: **{len(weekly)}**",
        "",
        "![Annual performance](performance.png)",
        "",
        "## Files",
        "",
        "- [`performance.csv`](performance.csv): session-level global wealth and within-year return curves.",
        "- [`weekly_holdings.csv`](weekly_holdings.csv): last-session portfolio for every market week.",
        "",
        "## Role note",
        "",
        str(annual["notes"]) if pd.notna(annual["notes"]) and str(annual["notes"]).strip() else "No additional role note.",
        "",
    ]
    write_lf(year_dir / "README.md", "\n".join(lines))


def main() -> None:
    configure_plot()
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    YEARS_DIR.mkdir(parents=True, exist_ok=True)

    daily = pd.read_csv(DATA_DIR / "daily_performance.csv", parse_dates=["date"])
    annual = pd.read_csv(DATA_DIR / "annual_performance.csv")
    weekly = pd.read_csv(HISTORY_DIR / "weekly_holdings.csv")

    for row_index, row in annual.iterrows():
        year = int(row["year"])
        year_dir = YEARS_DIR / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        curve = year_curve(daily, row)
        curve.to_csv(
            year_dir / "performance.csv",
            index=False,
            date_format="%Y-%m-%d",
            lineterminator="\n",
        )
        year_weekly = weekly.loc[weekly["year"].eq(year)].copy()
        year_weekly.to_csv(year_dir / "weekly_holdings.csv", index=False, lineterminator="\n")
        render_year_chart(curve, row, year_dir / "performance.png")
        write_year_readme(year_dir, row, year_weekly)

    render_cumulative_chart(daily, PERFORMANCE_DIR / "cumulative_performance.png")
    render_annual_returns(annual, PERFORMANCE_DIR / "annual_returns.png")

    performance_readme = [
        "# Performance renders",
        "",
        "The committed images are regenerated from the CSV files in `../data/` by running:",
        "",
        "```bash",
        "python scripts/render_reports.py",
        "```",
        "",
        "## Cumulative performance",
        "",
        "![Cumulative performance](cumulative_performance.png)",
        "",
        "## Annual returns",
        "",
        "![Annual returns](annual_returns.png)",
        "",
        "## Year-by-year results",
        "",
        *annual_table(annual),
        "",
    ]
    write_lf(PERFORMANCE_DIR / "README.md", "\n".join(performance_readme))

    years_readme = [
        "# Year index",
        "",
        "Every directory contains a performance render, session-level performance CSV, and weekly holding history. Training and validation years are intentionally retained.",
        "",
        *annual_table(annual),
        "",
    ]
    write_lf(YEARS_DIR / "README.md", "\n".join(years_readme))


if __name__ == "__main__":
    main()
