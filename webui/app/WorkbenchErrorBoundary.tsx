"use client";

import { Component, createRef, type ReactNode } from "react";

/** Imported rows live above this boundary, not in the subtree reset here. */
export class WorkbenchErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  private heading = createRef<HTMLHeadingElement>();

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    this.heading.current?.focus();
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="app-shell">
      <section className="workbench-recovery" role="alert" aria-labelledby="review-recovery-title">
        <h1 id="review-recovery-title" ref={this.heading} tabIndex={-1}>The review interface encountered an error</h1>
        <p>Your imported review rows and candidate stars remain in memory. Original files and the Sample Library have not been deleted.</p>
        <p>Reopen the interface to reset its view and filters without reimporting. Avoid refreshing or closing this tab: that clears a Review once import and session-only stars.</p>
        <button className="primary-button" onClick={() => this.setState({ failed: false })}>Reopen interface</button>
      </section>
    </main>;
  }
}
