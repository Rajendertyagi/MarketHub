export function DataRetentionPanel() {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Data &amp; Retention</h2>
      </div>
      <p className="form-hint">
        Storage lifecycle policies (news retention, cache policy, sync frequency, cleanup) are
        currently compiled-in defaults and will become configurable here in a later phase. Nothing
        on this panel changes behavior yet.
      </p>
    </div>
  );
}
