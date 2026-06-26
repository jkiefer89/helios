export function EmptyState({ title, body, actions = [] }: { title: string; body: string; actions?: string[] }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{body}</p>
      {actions.length > 0 && (
        <ul>
          {actions.map((action) => <li key={action}>{action}</li>)}
        </ul>
      )}
    </div>
  );
}
