"use client"

import { Search } from "lucide-react"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

interface Trial {
  id: string
  name: string
  phase: string
}

interface TriageHeaderProps {
  trials: Trial[]
  selectedTrial: string
  onTrialChange: (trialId: string) => void
  searchQuery: string
  onSearchChange: (query: string) => void
}

export function TriageHeader({
  trials,
  selectedTrial,
  onTrialChange,
  searchQuery,
  onSearchChange,
}: TriageHeaderProps) {
  // Page-level controls toolbar (the Vigil brand + global nav live in AppNav).
  return (
    <div className="border-b border-border bg-card">
      <div className="flex h-14 items-center justify-end px-6">
        <div className="flex items-center gap-4">
          <Select
            value={selectedTrial}
            onValueChange={(v) => v && onTrialChange(v)}
          >
            <SelectTrigger className="w-[240px] border-border bg-background text-sm font-medium shadow-none">
              <SelectValue placeholder="Select trial" />
            </SelectTrigger>
            <SelectContent className="border-border bg-card">
              {trials.map((trial) => (
                <SelectItem
                  key={trial.id}
                  value={trial.id}
                  className="text-sm focus:bg-muted"
                >
                  <span className="font-medium">{trial.name}</span>
                  <span className="ml-2 text-muted-foreground">
                    Phase {trial.phase}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Search participants..."
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              className="w-[220px] border-border bg-background pl-9 text-sm shadow-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
        </div>
      </div>
    </div>
  )
}
