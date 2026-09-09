import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

// Top-level error boundary. Keeps a single feature failure from blanking the
// whole app and shows a recoverable message.
export class GlobalErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface for diagnostics; no backend call.
    console.error("MarketHub UI error:", error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="state">
          <h2>Something went wrong</h2>
          <p className="muted">{this.state.error.message}</p>
          <button className="btn btn-primary" onClick={this.reset}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
