import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, MapPin } from "lucide-react";
import { Button } from "../components/ui/button";
import { bossCities, bossHotCities } from "./boss-cities";
import "./city-picker.css";

const cities = Object.entries(bossCities).flatMap(([province, rows]) =>
  rows.map(([name, pinyin]) => ({ name, pinyin, province })),
);
const cityByName = new Map(cities.map((item) => [item.name, item]));
const hotCities = bossHotCities.flatMap((name) => cityByName.get(name) ?? []);
const searchKey = (value: string) => value.normalize("NFKC").toLowerCase().replace(/[\s省市]/g, "");

type CityPickerProps = {
  city?: string;
  province?: string;
  onSelect: (city?: string, province?: string) => void;
};

export function CityPicker({ city, province, onSelect }: CityPickerProps) {
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [provinceFilter, setProvinceFilter] = useState("");

  useEffect(() => {
    if (open) search.current?.focus();
  }, [open]);

  const cityKey = searchKey(city ?? "");
  const provinceKey = searchKey(province ?? "");
  const keyword = searchKey(query);
  const options = keyword || provinceFilter ? cities.filter((option) =>
    (!provinceFilter || option.province === provinceFilter) &&
    searchKey(`${option.province}${option.name} ${option.pinyin}`).includes(keyword),
  ) : hotCities;

  function close() {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  }

  function select(name?: string, region?: string) {
    onSelect(name, region);
    close();
  }

  return <fieldset className="task-condition-row city-picker">
    <legend>求职城市</legend>
    <Button type="button" variant="secondary" className="city-picker-trigger" ref={trigger} aria-label="选择城市"
      aria-describedby={`${panelId}-value`}
      aria-controls={open ? panelId : undefined} aria-expanded={open} onClick={() => {
        if (open) {
          close();
        } else {
          setQuery("");
          setProvinceFilter("");
          setOpen(true);
        }
      }}>
      <MapPin size={16} aria-hidden="true" />
      <span id={`${panelId}-value`}>{city || "沿用当前城市"}</span>
      {city && province && cityKey !== provinceKey && <small>{province}</small>}
      <ChevronDown size={15} className="city-picker-chevron" aria-hidden="true" />
    </Button>
    {open && <div id={panelId} className="city-picker-panel" onKeyDown={(event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      close();
    }}>
      <div className="city-picker-controls">
        <label>
          <span className="sr-only">搜索城市</span>
          <input ref={search} type="search" value={query} onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
            placeholder="搜索城市或拼音" />
        </label>
        <label>
          <span className="sr-only">按省份浏览</span>
          <select value={provinceFilter} onChange={(event) => setProvinceFilter(event.target.value)}>
            <option value="">全部省份</option>
            {Object.keys(bossCities).map((name) => <option key={name}>{name}</option>)}
          </select>
        </label>
      </div>
      <div className="city-picker-caption">{keyword ? "搜索结果" : provinceFilter || "热门城市"}</div>
      <div className="task-filter-choices city-picker-options">
        {options.map((option) => <button type="button" key={`${option.province}:${option.name}`}
          aria-pressed={cityKey === searchKey(option.name) && (!provinceKey || provinceKey === searchKey(option.province))}
          onClick={() => select(option.name, option.province)}>
          {option.name}{!provinceFilter && option.name !== option.province && <small aria-hidden="true">{option.province}</small>}
        </button>)}
        {!options.length && <p role="status">未找到匹配的城市</p>}
      </div>
      <Button type="button" variant="ghost" className="city-picker-default" onClick={() => select()}>
        沿用当前城市
      </Button>
    </div>}
  </fieldset>;
}
