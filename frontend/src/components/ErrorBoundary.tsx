import { Component, type ErrorInfo, type ReactNode } from 'react';

interface State { error: Error | null }

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('App crashed:', error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ padding: 24, maxWidth: 720, margin: '40px auto', fontFamily: 'system-ui' }}>
        <h2 style={{ color: '#ff7a8a' }}>Something went wrong</h2>
        <pre style={{ whiteSpace: 'pre-wrap', background: '#141c2e', padding: 16, borderRadius: 12, color: '#e8ecf6' }}>
          {this.state.error.message}
          {'\n\n'}
          {this.state.error.stack}
        </pre>
        <button onClick={() => location.reload()} style={{ marginTop: 12, padding: '8px 16px' }}>Reload</button>
      </div>
    );
  }
}
