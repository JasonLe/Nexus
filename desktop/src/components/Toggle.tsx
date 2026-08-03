interface Props {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
}

export function Toggle({ checked, onChange, label }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2"
    >
      <span
        className={`relative inline-flex h-[18px] w-[34px] shrink-0 items-center rounded-full border transition-colors ${
          checked
            ? 'border-neon-500/60 bg-neon-500/25 shadow-glow-neon'
            : 'border-abyss-600 bg-abyss-700/60'
        }`}
      >
        <span
          className={`absolute h-3 w-3 rounded-full transition-all ${
            checked ? 'left-[18px] bg-neon-400' : 'left-[3px] bg-slate-500'
          }`}
        />
      </span>
      {label && <span className="text-[12.5px] text-slate-300">{label}</span>}
    </button>
  )
}
