/* Charts for the real, organization-scoped operational dashboard. */
(function () {
  "use strict";
  const payload = document.getElementById("dashboard-data");
  if (!payload || !window.Chart) return;
  const stats = JSON.parse(payload.textContent);
  const styles = getComputedStyle(document.body);
  const text = styles.getPropertyValue("--text-mid").trim();
  const muted = styles.getPropertyValue("--muted").trim();
  const border = styles.getPropertyValue("--border").trim();
  const blue = styles.getPropertyValue("--accent").trim() || "#3568d4";
  const grid = { color: border, drawBorder: false };
  const ticks = { color: muted, precision: 0, font: { size: 10 } };

  Chart.defaults.color = text;
  Chart.defaults.font.family = styles.getPropertyValue("--font").trim();

  const trend = document.getElementById("trend-chart");
  if (trend) {
    new Chart(trend, {
      type: "line",
      data: {
        labels: stats.incident_trend.labels,
        datasets: [{
          label: "Incidents",
          data: stats.incident_trend.values,
          borderColor: blue,
          backgroundColor: "rgba(53, 104, 212, 0.16)",
          fill: true,
          tension: 0.38,
          pointRadius: 2,
          pointHoverRadius: 5,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks },
          y: { beginAtZero: true, grid, ticks },
        },
      },
    });
  }

  const severity = document.getElementById("severity-chart");
  if (severity) {
    const hasValues = stats.severity_distribution.values.some(value => Number(value) > 0);
    new Chart(severity, {
      type: "doughnut",
      data: {
        labels: hasValues ? stats.severity_distribution.labels : ["No incidents"],
        datasets: [{
          data: hasValues ? stats.severity_distribution.values : [1],
          backgroundColor: hasValues
            ? ["#f0525f", "#e8aa3b", "#3568d4", "#3bc58c", "#77808e"]
            : ["rgba(155, 161, 170, 0.24)"],
          borderColor: "transparent",
          hoverOffset: 5,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: muted, boxWidth: 8, boxHeight: 8, padding: 14, font: { size: 9 } },
          },
        },
      },
    });
  }

  const heatmap = document.getElementById("heatmap-chart");
  if (heatmap) {
    const cells = [];
    for (let likelihood = 0; likelihood < 5; likelihood += 1) {
      for (let impact = 0; impact < 5; impact += 1) {
        cells.push({
          x: String(impact + 1),
          y: String(likelihood + 1),
          v: stats.risk_heatmap[likelihood][impact],
          score: (impact + 1) * (likelihood + 1),
        });
      }
    }
    new Chart(heatmap, {
      type: "matrix",
      data: {
        datasets: [{
          data: cells,
          borderColor: border,
          borderWidth: 1,
          borderRadius: 4,
          width: ({ chart }) => Math.max(18, (chart.chartArea?.width || 220) / 5 - 5),
          height: ({ chart }) => Math.max(18, (chart.chartArea?.height || 220) / 5 - 5),
          backgroundColor: context => {
            const cell = context.dataset.data[context.dataIndex];
            if (!cell.v) return "rgba(155, 161, 170, 0.10)";
            if (cell.score >= 15) return "rgba(240, 82, 95, 0.82)";
            if (cell.score >= 8) return "rgba(232, 170, 59, 0.78)";
            return "rgba(53, 104, 212, 0.72)";
          },
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: () => "",
              label: context => " " + context.raw.v + " risk record" + (context.raw.v === 1 ? "" : "s"),
            },
          },
        },
        scales: {
          x: { type: "category", labels: ["1", "2", "3", "4", "5"], title: { display: true, text: "Impact", color: text }, grid: { display: false }, ticks },
          y: { type: "category", labels: ["1", "2", "3", "4", "5"], title: { display: true, text: "Likelihood", color: text }, grid: { display: false }, ticks },
        },
      },
    });
  }
})();
