interface StatCardProps {
  label: string;
  value: string | number;
  tone?: "default" | "warning" | "danger" | "success";
}

const TONE_STYLES: Record<string, string> = {
  default: "text-slate-900",
  warning: "text-amber-600",
  danger: "text-red-600",
  success: "text-emerald-600",
};

export function StatCard({ label, value, tone = "default" }: StatCardProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${TONE_STYLES[tone]}`}>{value}</p>
    </div>
  );
}
