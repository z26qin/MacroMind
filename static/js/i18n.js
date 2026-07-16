// Display-layer country names. Data keys stay English everywhere.
window.MM = window.MM || {};
MM.i18n = {
  countryNames: {
    "United States of America": "美国",
    "Canada": "加拿大",
    "China": "中国",
    "Japan": "日本",
    "Brazil": "巴西",
    "Euro Area": "欧元区",
    "Argentina": "阿根廷",
    "Greece": "希腊",
    "Turkey": "土耳其",
  },
  // Fallback to the English key so unknown names degrade readably.
  display(name) { return MM.i18n.countryNames[name] || name; },
};
