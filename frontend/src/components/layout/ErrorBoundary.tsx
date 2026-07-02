import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/** Last-resort guard so a render crash shows a recoverable notice instead of a blank page. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Helios frontend crashed:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="view-stack" style={{ padding: "32px", maxWidth: "640px", margin: "0 auto" }}>
        <div className="notice danger" role="alert">
          Helios Pro hit an unexpected frontend error. Your local data and the Flask backend are unaffected.
        </div>
        <div className="empty-state">
          <strong>Something went wrong</strong>
          <p>{this.state.error.message || "An unexpected rendering error occurred."}</p>
          <button type="button" onClick={() => window.location.reload()}>Reload workspace</button>
        </div>
      </div>
    );
  }
}
