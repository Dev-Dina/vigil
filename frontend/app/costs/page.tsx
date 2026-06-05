import {
  MetricCard,
  PulseDivider,
  TrafficSplitBar,
  SpendChart,
  BudgetAlertList,
  StatusDot,
} from "@/components/vigil"

// TODO(phase4/5): wire to GET /monitoring/cost (CostReport) and GET /monitoring/models.
// CostReport fields are not yet defined in api.md — the values below are static
// placeholders pending that schema; do not treat as wired.
const metrics = {
  totalSpend: "$12,847",
  costPerPrediction: "$0.023",
  predictionsThisMonth: "558,291",
}

const trafficSegments = [
  { label: "Fast Model (GPT-4o-mini)", value: 72, color: "calm" as const },
  { label: "Precision Model (GPT-4o)", value: 28, color: "watch" as const },
]

const dailySpend = [
  { date: "May 26", value: 380 },
  { date: "May 27", value: 420 },
  { date: "May 28", value: 395 },
  { date: "May 29", value: 510 },
  { date: "May 30", value: 445 },
  { date: "May 31", value: 480 },
  { date: "Jun 1", value: 425 },
  { date: "Jun 2", value: 390 },
  { date: "Jun 3", value: 465 },
  { date: "Jun 4", value: 438 },
]

const budgetAlerts = [
  {
    id: "1",
    message: "Precision model usage exceeded 25% threshold for 3 consecutive days",
    status: "watch" as const,
    timestamp: "2024-06-04 09:15 UTC",
  },
  {
    id: "2",
    message: "Daily spend approaching 80% of budget limit ($500)",
    status: "watch" as const,
    timestamp: "2024-06-03 16:42 UTC",
  },
  {
    id: "3",
    message: "Unusual spike in predictions: 15% above daily average",
    status: "calm" as const,
    timestamp: "2024-06-02 11:30 UTC",
  },
]

export default function CostRoutingPage() {
  return (
    <div className="min-h-screen bg-background">
      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Cost &amp; Routing
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Model spend, traffic split, and routing rules
          </p>
        </div>
        {/* Metric cards */}
        <div className="grid grid-cols-3 gap-4 mb-8">
          <MetricCard
            title="Total Spend (MTD)"
            value={metrics.totalSpend}
            subtitle="Month to date"
            trend={{ value: 8.2, label: "vs last month" }}
          />
          <MetricCard
            title="Cost per Prediction"
            value={metrics.costPerPrediction}
            subtitle="Average this month"
            trend={{ value: -12.5, label: "vs last month" }}
          />
          <MetricCard
            title="Predictions This Month"
            value={metrics.predictionsThisMonth}
            subtitle="Total API calls"
            trend={{ value: 23.1, label: "vs last month" }}
          />
        </div>
        
        <PulseDivider />
        
        {/* Traffic split and spend chart */}
        <div className="grid grid-cols-2 gap-6 mb-8">
          <TrafficSplitBar
            segments={trafficSegments}
            title="Model Traffic Split"
          />
          <SpendChart
            data={dailySpend}
            title="Daily Spend (Last 10 Days)"
          />
        </div>
        
        {/* Model performance comparison */}
        <div className="grid grid-cols-2 gap-6 mb-8">
          <div className="bg-card border-[0.5px] border-border rounded-lg shadow-sm p-6">
            <h3 className="text-sm font-medium text-foreground mb-4">Fast Model</h3>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Model</span>
                <span className="font-mono text-sm text-foreground">GPT-4o-mini</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Cost per 1K tokens</span>
                <span className="font-mono text-sm text-foreground">$0.00015</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Avg latency</span>
                <span className="font-mono text-sm text-foreground">245ms</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Accuracy</span>
                <span className="font-mono text-sm text-status-calm">94.2%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Usage</span>
                <div className="flex items-center gap-2">
                  <StatusDot status="calm" size="sm" />
                  <span className="font-mono text-sm text-foreground">72%</span>
                </div>
              </div>
            </div>
          </div>
          
          <div className="bg-card border-[0.5px] border-border rounded-lg shadow-sm p-6">
            <h3 className="text-sm font-medium text-foreground mb-4">Precision Model</h3>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Model</span>
                <span className="font-mono text-sm text-foreground">GPT-4o</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Cost per 1K tokens</span>
                <span className="font-mono text-sm text-foreground">$0.005</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Avg latency</span>
                <span className="font-mono text-sm text-foreground">820ms</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Accuracy</span>
                <span className="font-mono text-sm text-status-calm">98.7%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Usage</span>
                <div className="flex items-center gap-2">
                  <StatusDot status="watch" size="sm" />
                  <span className="font-mono text-sm text-foreground">28%</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <PulseDivider />
        
        {/* Budget alerts */}
        <BudgetAlertList alerts={budgetAlerts} />
        
        {/* Routing rules summary */}
        <div className="mt-8 bg-card border-[0.5px] border-border rounded-lg shadow-sm p-6">
          <h3 className="text-sm font-medium text-foreground mb-4">Active Routing Rules</h3>
          <div className="space-y-3">
            <div className="flex items-start gap-3 p-3 rounded-md bg-muted/50 border-[0.5px] border-border">
              <div className="h-6 w-6 rounded bg-status-calm/10 flex items-center justify-center shrink-0">
                <span className="text-xs font-mono text-status-calm">1</span>
              </div>
              <div>
                <p className="text-sm text-foreground">
                  Route to Fast Model when risk score {"<"} 50
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Low-risk participants use cost-efficient model
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3 p-3 rounded-md bg-muted/50 border-[0.5px] border-border">
              <div className="h-6 w-6 rounded bg-status-watch/10 flex items-center justify-center shrink-0">
                <span className="text-xs font-mono text-status-watch">2</span>
              </div>
              <div>
                <p className="text-sm text-foreground">
                  Route to Precision Model when risk score {">="} 50
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Higher-risk participants get more accurate predictions
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3 p-3 rounded-md bg-muted/50 border-[0.5px] border-border">
              <div className="h-6 w-6 rounded bg-foreground/10 flex items-center justify-center shrink-0">
                <span className="text-xs font-mono text-foreground">3</span>
              </div>
              <div>
                <p className="text-sm text-foreground">
                  Always use Precision Model for first-time predictions
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Ensures accurate baseline for new participants
                </p>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
