const arabicDigits = "٠١٢٣٤٥٦٧٨٩";
const easternDigits = "۰۱۲۳۴۵۶۷۸۹";

export function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase().replace(/^#/, "").replace(/[\s_\-/]+/g, "");
}

export function normalizeFinancialNumber(value: string): number {
  const translated = value
    .replace(/[٠-٩]/g, digit => String(arabicDigits.indexOf(digit)))
    .replace(/[۰-۹]/g, digit => String(easternDigits.indexOf(digit)))
    .replace(/[،]/g, ",")
    .trim()
    .toUpperCase()
    .replace(/\s/g, "");
  const match = /^([+\-]?[\d,.]+)([KMB])?$/.exec(translated);
  if (!match) return Number.NaN;
  const base = Number(match[1].replace(/,/g, ""));
  const multiplier = match[2] === "K" ? 1_000 : match[2] === "M" ? 1_000_000 : match[2] === "B" ? 1_000_000_000 : 1;
  return Number.isFinite(base) ? base * multiplier : Number.NaN;
}
