// Placeholder values for initial local preview.
const placeholders = {
  "open-issues": 0,
  "created-24h": 0,
  "resolved-24h": 0,
  "sla-breaches": 0,
};

for (const [id, value] of Object.entries(placeholders)) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(value);
}
