import assert from "node:assert/strict"
import { describe, it } from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"

import { RiskBandBadge } from "./risk-band-badge"
import { RiskTrendSparkline } from "./risk-trend-sparkline"
import { WatchlistTable, type WatchlistRow } from "./watchlist-table"

describe("presentational watchlist components", () => {
  it("renders a risk band badge with the synthetic marker", () => {
    const html = renderToStaticMarkup(
      createElement(RiskBandBadge, {
        band: "high",
        marker: "synthetic",
      })
    )

    assert.match(html, /High/)
    assert.match(html, /Synthetic \/ non-clinical/)
  })

  it("renders a risk trend sparkline from visit scores", () => {
    const html = renderToStaticMarkup(
      createElement(RiskTrendSparkline, {
        scores: [12, 18, 11, 29],
      })
    )

    assert.match(html, /<svg/)
    assert.match(html, /Risk trend/)
    assert.match(html, /<path/)
  })

  it("renders a watchlist table from caller-scoped rows", () => {
    const rows: WatchlistRow[] = [
      {
        id: "row-1",
        participantLabel: "P-001",
        riskBand: "critical",
        riskScore: 91,
        trendScores: [44, 58, 73, 91],
        sponsorClass: "Industry",
        lastVisitLabel: "Visit 8",
        marker: "synthetic",
      },
    ]
    const html = renderToStaticMarkup(createElement(WatchlistTable, { rows }))

    assert.match(html, /P-001/)
    assert.match(html, /Critical/)
    assert.match(html, /Industry/)
    assert.match(html, /Risk trend/)
  })
})
