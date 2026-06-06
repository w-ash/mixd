/**
 * Schedule editor + summary for a workflow or sync target.
 *
 * Presentational and callback-driven (the parent wires the Orval mutations for
 * its specific target), so one component serves both `workflow schedule` and
 * `sync schedule`. Shows a plain-English summary + next-run when a schedule
 * exists, and a Daily/Weekly form (time, weekday, timezone) when editing or
 * when none exists yet. No cron — the friendly cadence IS the model.
 */

import { CalendarClock, Trash2 } from "lucide-react";
import { useState } from "react";

import type {
  ScheduleResponse,
  ScheduleUpsertRequest,
} from "#/api/generated/model";
import { ScheduleFailureBanner } from "#/components/shared/ScheduleFailureBanner";
import { Button } from "#/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "#/components/ui/select";
import { Switch } from "#/components/ui/switch";
import {
  browserTimezone,
  describeSchedule,
  formatNextRun,
  WEEKDAYS,
} from "#/lib/schedule";

interface SchedulePickerProps {
  schedule: ScheduleResponse | null;
  onSave: (req: ScheduleUpsertRequest) => void;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
  isPending?: boolean;
}

interface FormState {
  scheduleType: "daily" | "weekly";
  hour: number;
  minute: number;
  dayOfWeek: number;
  timezone: string;
}

function initialForm(schedule: ScheduleResponse | null): FormState {
  return {
    scheduleType: schedule?.schedule_type ?? "daily",
    hour: schedule?.hour ?? 6,
    minute: schedule?.minute ?? 30,
    dayOfWeek: schedule?.day_of_week ?? 0,
    timezone: schedule?.timezone ?? browserTimezone(),
  };
}

export function SchedulePicker({
  schedule,
  onSave,
  onToggle,
  onRemove,
  isPending = false,
}: SchedulePickerProps) {
  const [editing, setEditing] = useState(schedule === null);
  const [form, setForm] = useState<FormState>(() => initialForm(schedule));

  const timeValue = `${String(form.hour).padStart(2, "0")}:${String(
    form.minute,
  ).padStart(2, "0")}`;

  function handleSave() {
    onSave({
      schedule_type: form.scheduleType,
      hour: form.hour,
      minute: form.minute,
      day_of_week: form.scheduleType === "weekly" ? form.dayOfWeek : null,
      timezone: form.timezone,
    });
    setEditing(false);
  }

  // Summary view — a schedule exists and we're not editing it.
  if (schedule && !editing) {
    return (
      <div className="space-y-3 rounded-lg border-l-2 border-primary/60 bg-surface-elevated/50 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CalendarClock className="size-4 text-primary" />
            <span className="font-display text-sm text-text">
              {describeSchedule(schedule)}
            </span>
          </div>
          <Switch
            checked={schedule.status === "enabled"}
            disabled={isPending}
            aria-label="Enabled"
            onCheckedChange={(checked) => onToggle(checked)}
          />
        </div>

        <p className="font-mono text-xs text-text-muted">
          {schedule.status === "enabled"
            ? `Next run: ${formatNextRun(schedule)}`
            : "Paused — won't run until re-enabled"}
        </p>

        <ScheduleFailureBanner schedule={schedule} />

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={isPending}
            onClick={() => {
              setForm(initialForm(schedule));
              setEditing(true);
            }}
          >
            Edit schedule
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={isPending}
            className="gap-1.5 text-text-muted hover:text-secondary"
            onClick={onRemove}
          >
            <Trash2 className="size-3.5" />
            Remove
          </Button>
        </div>
      </div>
    );
  }

  // Editor view — creating a schedule, or editing the existing one.
  const preview = describeSchedule({
    schedule_type: form.scheduleType,
    hour: form.hour,
    minute: form.minute,
    day_of_week: form.scheduleType === "weekly" ? form.dayOfWeek : null,
    timezone: form.timezone,
  });

  return (
    <div className="space-y-4 rounded-lg border-l-2 border-border-muted bg-surface-elevated/50 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <span className="font-display text-xs text-text-muted">Cadence</span>
          <Select
            value={form.scheduleType}
            onValueChange={(value) =>
              setForm((f) => ({
                ...f,
                scheduleType: value as "daily" | "weekly",
              }))
            }
          >
            <SelectTrigger aria-label="Cadence" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="daily">Daily</SelectItem>
              <SelectItem value="weekly">Weekly</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {form.scheduleType === "weekly" && (
          <div className="flex flex-col gap-1">
            <span className="font-display text-xs text-text-muted">Day</span>
            <Select
              value={String(form.dayOfWeek)}
              onValueChange={(value) =>
                setForm((f) => ({ ...f, dayOfWeek: Number(value) }))
              }
            >
              <SelectTrigger aria-label="Day of week" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WEEKDAYS.map((name, index) => (
                  <SelectItem key={name} value={String(index)}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <label className="flex flex-col gap-1">
          <span className="font-display text-xs text-text-muted">Time</span>
          <input
            type="time"
            aria-label="Time of day"
            value={timeValue}
            onChange={(e) => {
              const [h, m] = e.target.value.split(":");
              setForm((f) => ({
                ...f,
                hour: Number(h) || 0,
                minute: Number(m) || 0,
              }));
            }}
            className="h-9 rounded-md border border-border bg-surface px-2 font-mono text-sm text-text"
          />
        </label>

        <label className="flex min-w-40 flex-1 flex-col gap-1">
          <span className="font-display text-xs text-text-muted">Timezone</span>
          <input
            type="text"
            aria-label="Timezone"
            value={form.timezone}
            onChange={(e) =>
              setForm((f) => ({ ...f, timezone: e.target.value }))
            }
            className="h-9 rounded-md border border-border bg-surface px-2 font-mono text-sm text-text"
          />
        </label>
      </div>

      <p className="font-body text-sm text-text-muted">{preview}</p>

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={isPending} onClick={handleSave}>
          {schedule ? "Update schedule" : "Schedule"}
        </Button>
        {schedule && (
          <Button
            variant="ghost"
            size="sm"
            disabled={isPending}
            onClick={() => {
              setForm(initialForm(schedule));
              setEditing(false);
            }}
          >
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}
