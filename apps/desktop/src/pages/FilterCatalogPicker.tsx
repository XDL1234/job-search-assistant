import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { Button } from "../components/ui/button";

export type CatalogOption = {
  value: readonly string[];
  group: string;
  section?: string;
};

const limit = 10;
const keyOf = (labels: readonly string[]) => JSON.stringify(labels);
const unrestricted = ["不限"];

export function FilterCatalogPicker({ label, showLabel = true, options, value, onChange }: {
  label: string;
  showLabel?: boolean;
  options: readonly CatalogOption[];
  value: string[][];
  onChange: (value: string[][]) => void;
}) {
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState("");
  const [query, setQuery] = useState("");
  const groups = Array.from(new Set(options.map((option) => option.group)));
  const activeGroup = group || groups[0] || "";
  const search = query.trim().toLowerCase();
  const selected = new Set(value.map(keyOf));
  const hasUnrestricted = selected.has(keyOf(unrestricted));
  useEffect(() => {
    if (open) searchBox.current?.focus();
  }, [open]);
  const sections = new Map<string, CatalogOption[]>();
  if (open) for (const option of options) {
    const { group, section, value } = option;
    if (search ? ![group, section, ...value].join(" ").toLowerCase().includes(search) : group !== activeGroup) continue;
    const heading = search ? [group, section].filter(Boolean).join(" › ") : section || "";
    const matches = sections.get(heading) || [];
    matches.push(option);
    sections.set(heading, matches);
  }

  const remove = (key: string) => onChange(value.filter((item) => keyOf(item) !== key));

  return <fieldset className="task-condition-row">
    <legend className={showLabel ? undefined : "sr-only"}>{label}</legend>
    <div className="task-catalog-summary">
      <Button ref={trigger} type="button" variant="secondary" aria-expanded={open} aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}>选择{label}<ChevronDown size={15} aria-hidden="true" /></Button>
      {!value.length && <span className="muted">沿用 BOSS 当前条件</span>}
      {!!value.length && <div className="task-catalog-selected task-filter-choices">
        {value.map((item) => {
          const key = keyOf(item);
          const path = item.join(" › ");
          return <button type="button" key={key} title={path} aria-label={`移除${label} ${path}`} onClick={() => remove(key)}>
            {path}<X size={13} aria-hidden="true" />
          </button>;
        })}
      </div>}
    </div>
    {open && <div className="task-catalog-panel" id={panelId} onKeyDown={(event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      trigger.current?.focus({ preventScroll: true });
    }}>
      <div className="task-catalog-toolbar">
        <input ref={searchBox} type="search" aria-label={`搜索${label}`} placeholder={`搜索${label}`} value={query}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
          onChange={(event) => setQuery(event.target.value)} />
        {groups.length > 1 && !search && <select aria-label={`${label}大类`} value={activeGroup} onChange={(event) => setGroup(event.target.value)}>
          {groups.map((name) => <option key={name}>{name}</option>)}
        </select>}
      </div>
      <div className="task-filter-choices task-catalog-actions">
        <button type="button" aria-pressed={!value.length} onClick={() => onChange([])}>沿用当前</button>
        <button type="button" aria-pressed={hasUnrestricted} onClick={() => onChange([unrestricted])}>不限</button>
        <span className="muted">已选 {hasUnrestricted ? 0 : value.length}/{limit}</span>
      </div>
      <div className="task-catalog-results" tabIndex={0} role="region" aria-label={`${label}列表`}>
        {Array.from(sections, ([heading, entries]) => <div key={heading} className="task-catalog-group">
          {heading && <p>{heading}</p>}
          <div className="task-filter-choices">{entries.map(({ value: option }) => {
            const key = keyOf(option);
            const checked = selected.has(key);
            return <button type="button" key={key} aria-pressed={checked} title={option.join(" › ")}
              disabled={!checked && !hasUnrestricted && value.length >= limit}
              onClick={() => checked ? remove(key) : onChange([...(hasUnrestricted ? [] : value), [...option]])}>
              {option.at(-1)}
            </button>;
          })}</div>
        </div>)}
        {!sections.size && <p className="muted">没有匹配的选项。</p>}
      </div>
    </div>}
  </fieldset>;
}
