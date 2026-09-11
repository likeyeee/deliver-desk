import React from "react";
import regions from "./data/regions.json";

const nationwide = { name: "全国", cities: ["全国"] };
export default function CityPicker({ city, onChange, allowNationwide = true }) {
  const provinces = [
    ...(allowNationwide ? [nationwide] : []),
    ...regions.provinces,
  ];
  // Derive the province from the existing city setting so older configurations
  // and imports keep their destination without a second, conflicting value.
  const province = provinces.find((item) => item.cities.includes(city));
  const cities = province?.cities || [city];
  return (
    <>
      <label className="field">
        <span>省份 / 地区</span>
        <select
          aria-label="省份 / 地区"
          value={province?.name || "saved-city"}
          onChange={(event) => {
            const next = provinces.find(
              (item) => item.name === event.target.value,
            );
            if (next) onChange(next.cities[0]);
          }}
        >
          {!province && (
            <option value="saved-city">
              {city === "全国" ? "请选择具体城市" : "已保存城市"}
            </option>
          )}
          {provinces.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>工作城市</span>
        <select
          aria-label="工作城市"
          value={city}
          disabled={province === nationwide}
          onChange={(event) => onChange(event.target.value)}
        >
          {cities.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}
