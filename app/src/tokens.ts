// "Ink & Lamplight", the palette the owner chose (design doc, "The look"). index.css defines the
// same values as CSS variables; this copy is for places that need a value in code (charts, tests).
export const PALETTE = {
  ink: "#0F1319", // page background
  panel: "#181D25", // raised surfaces: cards, side panel, palette, menus
  mist: "#E6E9EE", // primary text; the fill of primary buttons
  mist2: "#949CA8", // secondary text; the source of every tint below
  lamp: "#E6AE5C", // the accent: hover, focus, top pick, current page, urgent deadlines, success glow
  alert: "#E07A6E", // hard failures only, always with a warning icon and words
  border: "#333942", // mist-2 at 22% over panel
  hover: "#22272F", // mist at 5% over panel
  disabled: "#5C636D", // mist-2 at 55% over panel
  glow: "#362F25", // lamp at 18% over panel
} as const

// The email kinds' dot colours (design: 2026-11-18-email-kinds); index.css defines them as --kind-*.
export const KIND_COLOURS = {
  study: "#7DD3FC", // light blue: subjects and the degree program's office
  uni_admin: "#A78BFA", // violet
  hackathon: "#FB923C", // orange
  jobs: "#FACC15", // yellow, brighter than the lamp so it never reads as the highlight
  scholarship: "#E879F9", // pink
  news_events: "#4ADE80", // green
  uni_mass: "#6B7280", // the four grey kinds recede on ink
  social: "#94A3B8",
  promo: "#C8A97E",
  accounts: "#E5E7EB",
} as const
