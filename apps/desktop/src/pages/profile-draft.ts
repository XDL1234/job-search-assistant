export type Source = { locator: string; original?: string; edited?: boolean };
export type ProfileData = Record<string, unknown> & { sources?: Record<string, Source> };
export type Extraction = {
  source_file: string;
  source_sha256: string;
  profile: ProfileData;
  sources: Record<string, Source>;
  warnings?: string[];
  ocr_pages?: number[];
};

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function entries(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  return value == null || value === "" ? [] : [value];
}

const identityFields: Record<string, string[]> = {
  education: ["school", "degree", "start_date", "end_date"],
  internships: ["company", "role", "start_date", "end_date"],
  work_experience: ["company", "role", "start_date", "end_date"],
  projects: ["name", "start_date", "end_date"],
  awards: ["name", "date"],
  languages: ["language"],
};
const olderNames: Record<string, string[]> = {
  school: ["school_name", "university"],
  company: ["company_name", "organization"],
  name: ["title", "project_name", "award_name"],
  language: ["name"],
};

function normalized(value: unknown) {
  return typeof value === "string" || typeof value === "number" ?
    String(value).normalize("NFKC").replace(/\s+/g, "").toLowerCase() : "";
}

function identity(entry: Record<string, unknown>, key: string) {
  return [key, ...(olderNames[key] ?? [])].map((name) => normalized(entry[name])).find(Boolean) ?? "";
}

function comparable(value: unknown, field: string) {
  const text = normalized(value);
  if (!field.endsWith("date")) return text;
  const date = /^(\d{4})(?:[./年-](\d{1,2})(?:[./月-](\d{1,2}))?)?[月日]?$/.exec(text);
  return date ? [date[1], ...date.slice(2).filter(Boolean).map((part) => part.padStart(2, "0"))].join("-") : text;
}

function sameEntry(group: string, left: unknown, right: Record<string, unknown>) {
  const [primary, ...qualifiers] = identityFields[group];
  const key = identity(right, primary);
  if (typeof left === "string") {
    return Boolean(left.trim() && normalized(left) === normalized(right.description)) ||
      Boolean(key && [left, left.split(/[：:；;｜|]/, 1)[0]].some((label) => normalized(label) === key));
  }
  const old = record(left);
  if (!key || identity(old, primary) !== key) return false;
  return qualifiers.every((field) => {
    const a = comparable(old[field], field);
    const b = comparable(right[field], field);
    return !a || !b || a === b;
  });
}

function hasValue(value: unknown) {
  return typeof value === "string" ? Boolean(value.trim()) : value != null;
}

function isFormValue(value: unknown, field: string) {
  if (field === "current") return typeof value === "boolean";
  return (typeof value === "string" && Boolean(value.trim())) || (typeof value === "number" && Number.isFinite(value));
}

export function fillFromResume(current: ProfileData, extraction: Extraction, fields: string[],
  groups: { key: string; fields: { key: string }[]; ongoing?: boolean }[],
  edited: ReadonlySet<string>, removed: Record<string, unknown[]>, filledGroups?: ReadonlySet<string>) {
  const next: ProfileData = { ...current };
  const changed = new Set<string>();
  for (const path of fields) {
    const [key, child] = path.split(".");
    const value = child ? record(extraction.profile[key])[child] : extraction.profile[key];
    const old = child ? record(current[key])[child] : current[key];
    if (edited.has(path) || hasValue(old) || !isFormValue(value, key)) continue;
    if (child) next[key] = { ...record(next[key]), [child]: value };
    else next[key] = value;
    changed.add(path);
    changed.add(key);
  }
  for (const group of groups) {
    const { key } = group;
    const rows = [...entries(current[key])];
    const original = current.sources?.[key]?.original;
    const alreadyFilled = filledGroups?.has(key) || (original && original === extraction.sources[key]?.original);
    const allowed = group.fields.map((field) => field.key);
    if (group.ongoing) allowed.push("current");
    for (const raw of entries(extraction.profile[key])) {
      const candidate = Object.fromEntries(Object.entries(record(raw))
        .filter(([name, value]) => allowed.includes(name) && isFormValue(value, name)));
      const primary = identityFields[key][0];
      if (!identity(candidate, primary) || removed[key]?.some((entry) => sameEntry(key, entry, candidate))) continue;
      const matches = rows.map((entry, index) => sameEntry(key, entry, candidate) ? index : -1).filter((index) => index >= 0);
      if (!matches.length) {
        if (alreadyFilled) continue;
        rows.push(candidate);
        changed.add(key);
      } else if (matches.length === 1 && typeof rows[matches[0]] !== "string") {
        const index = matches[0];
        const old = record(rows[index]);
        const missing = Object.entries(candidate).filter(([field]) => old[field] == null &&
          !(field === "end_date" && old.current === true) && !(field === "current" && hasValue(old.end_date)));
        if (missing.length) {
          rows[index] = { ...old, ...Object.fromEntries(missing) };
          changed.add(key);
        }
      }
    }
    if (changed.has(key)) next[key] = rows;
  }
  if (!changed.size) return undefined;
  const sources = Object.fromEntries(Object.entries(extraction.sources).filter(([path]) => changed.has(path)));
  const { source_file, source_sha256 } = extraction;
  const baseline = Object.fromEntries(Object.entries(extraction.profile).filter(([path]) => changed.has(path)));
  return { profile: next, extraction: { source_file, source_sha256, profile: baseline, sources } };
}
