import type { ReactNode } from "react";

export function EmptyState({ title, body, actions = [], children }: {
  title: string; body: string; actions?: string[]; children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{body}</p>
      {actions.length > 0 && (
        <ul>
          {actions.map((action) => <li key={action}>{action}</li>)}
        </ul>
      )}
      {children}
    </div>
  );
}
