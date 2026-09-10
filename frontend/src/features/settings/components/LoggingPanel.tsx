export function LoggingPanel() {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Logging</h2>
      </div>
      <p className="form-hint">
        Live log viewing lives on the operational Logs page, fed by the in-memory
        ring buffer and SSE stream.
      </p>
      <a className="btn" href="#/logs">
        Open Live Logs
      </a>
    </div>
  );
}
