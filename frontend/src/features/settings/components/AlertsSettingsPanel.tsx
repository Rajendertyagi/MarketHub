export function AlertsSettingsPanel() {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Alerts</h2>
      </div>
      <p className="form-hint">
        Price alerts are managed on the operational Alerts screen, where active
        alerts, trigger history, and live push notifications live.
      </p>
      <a className="btn" href="#/alerts">
        Open Alerts
      </a>
    </div>
  );
}
