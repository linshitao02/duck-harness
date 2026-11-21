export const DUCKS = [
  { id: "chick", label: "小鸭" },
  { id: "rubber", label: "橡皮鸭" },
  { id: "swim", label: "游水" },
  { id: "fly", label: "起飞" },
  { id: "hatch", label: "出壳" },
  { id: "nap", label: "打盹" },
  { id: "sailor", label: "水手" },
  { id: "waddle", label: "带小鸭" },
];

export const GROUPS = [
  { id: "flock", label: "一群" },
  { id: "talk", label: "对话" },
  { id: "ring", label: "围坐" },
  { id: "relay", label: "交接" },
  { id: "row", label: "并排" },
  { id: "huddle", label: "聚拢" },
];

export function isDuck(id) {
  return DUCKS.some((item) => item.id === id);
}

export function isGroup(id) {
  return GROUPS.some((item) => item.id === id);
}

export function badgeStyle(iconKey, iconColor) {
  const color = iconColor || "#ff4b93";
  if (isDuck(iconKey) || isGroup(iconKey)) return { color, background: "transparent" };
  return { background: color, color: "#fff" };
}

const LEGACY_SHAPES = [
  { id: "heart", d: "M32 54C20 44 8 36 8 24a12 12 0 0 1 22-7 12 12 0 0 1 22 7c0 12-12 20-20 30z" },
  { id: "circle", d: "M32 8a24 24 0 1 0 .1 0z" },
  { id: "squircle", d: "M20 8h24a12 12 0 0 1 12 12v24a12 12 0 0 1-12 12H20a12 12 0 0 1-12-12V20A12 12 0 0 1 20 8z" },
  { id: "diamond", d: "M32 6l20 26L32 58 12 32z" },
  { id: "star", d: "M32 6l7 16h16l-13 10 5 17-15-10-15 10 5-17L9 22h16z" },
  { id: "flower", d: "M32 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm14 6a8 8 0 1 1 16 0 8 8 0 0 1-16 0zM46 40a8 8 0 1 1 0 16 8 8 0 0 1 0-16zM18 40a8 8 0 1 1 0 16 8 8 0 0 1 0-16zM4 24a8 8 0 1 1 16 0 8 8 0 0 1-16 0zM24 24a8 8 0 1 0 16 0 8 8 0 0 0-16 0z" },
  { id: "blob", d: "M22 10c10-6 24-2 28 10 4 10-2 16 0 24-2 10-14 16-24 14S6 48 8 34 12 16 22 10z" },
  { id: "drop", d: "M32 6c10 14 18 24 18 34a18 18 0 0 1-36 0C14 30 22 20 32 6z" },
  { id: "triangle", d: "M32 8l24 44H8z" },
  { id: "pill", d: "M16 20h32a12 12 0 0 1 0 24H16a12 12 0 0 1 0-24z" },
  { id: "house", d: "M8 30l24-18 24 18v24a4 4 0 0 1-4 4H12a4 4 0 0 1-4-4z" },
  { id: "burst", d: "M32 4l6 14 14-4-6 14 14 6-14 6 6 14-14-6-6 14-6-14-14 6 6-14-14-6 14-6-6-14 14 4z" },
];

export const BOT_COLORS = [
  "#1c1c1e",
  "#8d5a3c",
  "#e23b3b",
  "#f08a24",
  "#f5c542",
  "#3cbe5a",
  "#2fbfb0",
  "#3b82f6",
  "#7c4dff",
  "#ff4b93",
  "#9aa0a6",
];

export function shapeById(id) {
  return LEGACY_SHAPES.find((item) => item.id === id) || null;
}
